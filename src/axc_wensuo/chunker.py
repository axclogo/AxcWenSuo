"""Chunking rules FR-401..FR-409. 切段规则（FR-401~FR-409）。"""

from __future__ import annotations

import bisect
import hashlib
from dataclasses import dataclass, field

from axc_wensuo.models import Location
from axc_wensuo.parsers.base import Block, ParsedDocument, heading_title, render_table

CHUNKER_VERSION = 2
SENTENCE_CHARS = frozenset('。！？；.!?;')


@dataclass
class ChunkDraft:
	text: str
	title_path: list[str]
	location: Location
	is_table: bool = False
	ordinal: int = 0
	chunk_id: str = ''
	index_text: str = ''

@dataclass
class _Atom:
	text: str
	location: Location
	start: int
	end: int
	code: bool = False  # Whole code block above max_chars. 超过 max_chars 的整块代码。

@dataclass
class _Unit:
	blocks: list[Block] = field(default_factory=list)
	title_path: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Limits:
	"""Chunk size limits. 切段长度参数。"""

	max_chars: int
	min_chars: int

def chunk_document(
	doc: ParsedDocument,
	*,
	scope: str,
	doc_title: str,
	max_chars: int = 1200,
	min_chars: int = 80,
) -> list[ChunkDraft]:
	drafts: list[ChunkDraft] = []
	line_starts = _line_starts(doc.text)
	for unit in _merge_light_units(doc, _units(doc.blocks), min_chars):
		drafts.extend(_chunk_unit(doc, unit, line_starts, _Limits(max_chars, min_chars)))
	drafts = _merge_short(drafts, min_chars, max_chars)
	_finalize(drafts, doc.doc_id, doc_title)
	return drafts

def _line_starts(text: str) -> list[int]:
	starts = [0]
	for index, char in enumerate(text):
		if char == '\n':
			starts.append(index + 1)
	return starts

def _line_number(line_starts: list[int], offset: int) -> int:
	"""1-based line number. 1 起始行号。"""
	position = bisect.bisect_right(line_starts, max(offset, 0))
	return max(1, position)

def _units(blocks: list[Block]) -> list[_Unit]:
	units: list[_Unit] = []
	stack: list[tuple[int, str]] = []
	for block in blocks:
		if block.kind == 'heading':
			level = block.level or 1
			while stack and stack[-1][0] >= level:
				stack.pop()
			stack.append((level, heading_title(block)))
			units.append(_Unit(blocks=[block], title_path=[title for _, title in stack]))
			continue
		if not units:
			units.append(_Unit())
		units[-1].blocks.append(block)
	for unit in units:
		while unit.blocks and unit.blocks[-1].kind == 'rule':
			unit.blocks.pop()
	return [unit for unit in units if unit.blocks]

def _merge_light_units(doc: ParsedDocument, units: list[_Unit], min_chars: int) -> list[_Unit]:
	"""Prepend a light unit to its first sub-section. 过短的标题单元并入其后的子章节。"""
	merged: list[_Unit] = []
	carry: list[Block] = []
	for index, unit in enumerate(units):
		blocks = carry + unit.blocks
		carry = []
		following = units[index + 1] if index + 1 < len(units) else None
		light = len(_unit_text(doc, blocks)) < min_chars and not any(block.kind == 'table' for block in blocks)
		if light and following is not None and _is_descendant(unit.title_path, following.title_path):
			carry = blocks
			continue
		merged.append(_Unit(blocks=blocks, title_path=unit.title_path))
	return merged

def _is_descendant(parent: list[str], child: list[str]) -> bool:
	return len(child) > len(parent) and child[: len(parent)] == parent

def _unit_text(doc: ParsedDocument, blocks: list[Block]) -> str:
	"""Prefer the original source slice. 优先取原文切片。"""
	return doc.text[blocks[0].start : blocks[-1].end]

def _union_location(blocks: list[Block]) -> Location:
	location = blocks[0].location_or_lines()
	for block in blocks[1:]:
		location = location.union(block.location_or_lines())
	return location

def _chunk_unit(doc: ParsedDocument, unit: _Unit, line_starts: list[int], limits: _Limits) -> list[ChunkDraft]:
	blocks = unit.blocks
	has_table = any(block.kind == 'table' for block in blocks)
	unit_text = _unit_text(doc, blocks)
	if not has_table and len(unit_text) <= limits.max_chars:
		return [ChunkDraft(text=unit_text, title_path=list(unit.title_path), location=_union_location(blocks))]

	drafts: list[ChunkDraft] = []
	segment: list[Block] = []
	for block in blocks:
		if block.kind != 'table':
			segment.append(block)
			continue
		caption = _take_caption(segment, limits.min_chars)
		before = _pack_blocks(doc, segment, unit.title_path, line_starts, limits) if segment else []
		lead = before.pop() if before and len(before[-1].text) < limits.min_chars else None
		drafts.extend(before)
		drafts.extend(_table_chunks(_TableInput(doc, block, caption, lead), unit.title_path, limits.max_chars))
		segment = []
	if segment:
		drafts.extend(_pack_blocks(doc, segment, unit.title_path, line_starts, limits))
	return drafts

def _take_caption(segment: list[Block], min_chars: int) -> Block | None:
	"""Pop the short paragraph right before a table. 取出紧邻表格前的短段落作为表格标题。"""
	if segment and segment[-1].kind == 'paragraph' and segment[-1].end - segment[-1].start <= min_chars:
		return segment.pop()
	return None

def _pack_blocks(
	doc: ParsedDocument,
	blocks: list[Block],
	title_path: list[str],
	line_starts: list[int],
	limits: _Limits,
) -> list[ChunkDraft]:
	drafts: list[ChunkDraft] = []
	current: list[_Atom] = []
	for block in blocks:
		head = block.start - current[0].start if current and _span(current) < limits.min_chars else 0
		for atom in _block_atoms(doc, block, line_starts, limits, head):
			if current and not _joins(current, atom, limits):
				drafts.append(_draft_from_atoms(doc, current, title_path))
				current = []
			current.append(atom)
	if current:
		drafts.append(_draft_from_atoms(doc, current, title_path))
	return drafts

def _span(atoms: list[_Atom]) -> int:
	return atoms[-1].end - atoms[0].start

def _joins(current: list[_Atom], atom: _Atom, limits: _Limits) -> bool:
	"""Whether the atom fits into the current chunk. 原子能否装入当前段。"""
	total = atom.end - current[0].start
	if total <= limits.max_chars:
		return True
	# A short head may join a whole code block up to 2 x max_chars. 短前导可并入不超过 2 倍上限的整块代码。
	return atom.code and _span(current) < limits.min_chars and total <= 2 * limits.max_chars

def _draft_from_atoms(doc: ParsedDocument, atoms: list[_Atom], title_path: list[str]) -> ChunkDraft:
	text = doc.text[atoms[0].start : atoms[-1].end]
	location = atoms[0].location
	for atom in atoms[1:]:
		location = location.union(atom.location)
	return ChunkDraft(text=text, title_path=list(title_path), location=location)

def _block_atoms(
	doc: ParsedDocument, block: Block, line_starts: list[int], limits: _Limits, head: int
) -> list[_Atom]:
	"""Split one block; head is the length of the short fragment before it. 切分一个块；head 为其前短前导的长度。"""
	text = block.text
	code = block.kind == 'code'
	if len(text) + head <= (2 if code else 1) * limits.max_chars:
		whole_code = code and len(text) + head > limits.max_chars
		return [_Atom(text, block.location_or_lines(), block.start, block.end, code=whole_code)]
	atoms: list[_Atom] = []
	for start, end in _split_spans(text, limits.max_chars - head, limits.max_chars, code=code):
		location = _piece_location(doc, block, start, end, line_starts)
		atoms.append(_Atom(text[start:end], location, block.start + start, block.start + end))
	return atoms

def _split_spans(text: str, first_limit: int, max_chars: int, *, code: bool) -> list[tuple[int, int]]:
	spans = _line_spans(text) if code else _sentence_spans(text)
	result: list[tuple[int, int]] = []
	chunk_start, chunk_end, limit = spans[0][0], 0, first_limit
	for start, end in spans:
		if end - chunk_start <= limit:
			chunk_end = end
			continue
		if chunk_end > chunk_start:
			result.append((chunk_start, chunk_end))
			chunk_start, limit = start, max_chars
		while end - chunk_start > limit:
			result.append((chunk_start, chunk_start + limit))
			chunk_start, limit = chunk_start + limit, max_chars
		chunk_end = end
	if chunk_end > chunk_start:
		result.append((chunk_start, chunk_end))
	return result

def _sentence_spans(text: str) -> list[tuple[int, int]]:
	spans: list[tuple[int, int]] = []
	start = 0
	for index, char in enumerate(text):
		if char in SENTENCE_CHARS or char == '\n':
			spans.append((start, index + 1))
			start = index + 1
	if start < len(text):
		spans.append((start, len(text)))
	return spans

def _line_spans(text: str) -> list[tuple[int, int]]:
	spans: list[tuple[int, int]] = []
	offset = 0
	for line in text.splitlines(keepends=True):
		spans.append((offset, offset + len(line)))
		offset += len(line)
	return spans

def _piece_location(doc: ParsedDocument, block: Block, start: int, end: int, line_starts: list[int]) -> Location:
	if block.location is not None and block.line_start is None:
		return block.location
	if block.line_start is None or block.line_end is None:
		return Location(kind='section')
	start_line = _line_number(line_starts, block.start + start)
	end_line = _line_number(line_starts, block.start + max(start, end - 1))
	return Location(kind='lines', start=start_line, end=end_line)

@dataclass(frozen=True)
class _TableInput:
	"""A table with its caption and leading fragment. 表格及其标题、前导短段。"""

	doc: ParsedDocument
	block: Block
	caption: Block | None
	lead: ChunkDraft | None


def _table_chunks(item: _TableInput, title_path: list[str], max_chars: int) -> list[ChunkDraft]:
	block, table = item.block, item.block.table
	if table is None or not table.rows:
		return []
	header_rows = max(1, min(table.header_rows, len(table.rows)))
	header, data = table.rows[:header_rows], table.rows[header_rows:]
	caption = item.doc.text[item.caption.start : item.caption.end] + '\n\n' if item.caption is not None else ''
	lead = item.lead
	drafts: list[ChunkDraft] = []
	if lead is not None and len(f'{lead.text}\n\n{caption}{render_table(header + data[:1], header_rows)}') > max_chars:
		drafts.append(lead)
		lead = None
	lead_text = f'{lead.text}\n\n' if lead is not None else ''

	def render(rows: list[list[str]], first: bool) -> str:
		return (lead_text if first else '') + caption + render_table(header + rows, header_rows)

	start = 0
	while start == 0 or start < len(data):
		end = start + 1
		while end < len(data) and len(render(data[start : end + 1], start == 0)) <= max_chars:
			end += 1
		location = _table_location(block, table, header_rows, start, end) if data else block.location_or_lines()
		if start == 0:
			location = _first_table_location(item, lead, location)
		drafts.append(ChunkDraft(render(data[start:end], start == 0), list(title_path), location, is_table=True))
		start = end
	return drafts

def _first_table_location(item: _TableInput, lead: ChunkDraft | None, location: Location) -> Location:
	"""Extend the first chunk back over caption and lead. 第一个表格段的位置向前覆盖标题与前导。"""
	if item.caption is not None:
		location = item.caption.location_or_lines().union(location)
	if lead is not None:
		location = lead.location.union(location)
	return location

def _row_end_line(table, index: int) -> int | None:
	"""Last source line of a row. 一行记录的最后一个源行号。"""
	if index < len(table.row_end_lines):
		return table.row_end_lines[index]
	return table.row_lines[index]

def _table_location(block: Block, table, header_rows: int, start: int, end: int) -> Location:
	location = block.location_or_lines()
	if location.kind == 'sheet':
		base = location.row_start or 1
		return Location(
			kind='sheet',
			sheet=location.sheet,
			row_start=base + start,
			row_end=base + end - 1,
		)
	if location.kind in ('page', 'slide'):
		return location
	if table.row_lines:
		first_index = header_rows + start
		last_index = header_rows + end - 1
		if first_index < len(table.row_lines) and last_index < len(table.row_lines):
			first = table.row_lines[first_index]
			last = _row_end_line(table, last_index)
			if first is not None and last is not None:
				return Location(kind='lines', start=min(first, last), end=max(first, last))
	return location

def _merge_short(drafts: list[ChunkDraft], min_chars: int, max_chars: int) -> list[ChunkDraft]:
	"""Absorb fragments shorter than min_chars (FR-406). 吸收短于 min_chars 的碎片段。"""
	merged: list[ChunkDraft] = []
	for position, draft in enumerate(drafts):
		if len(draft.text) >= min_chars:
			merged.append(draft)
			continue
		previous = merged[-1] if merged else None
		following = drafts[position + 1] if position + 1 < len(drafts) else None
		if not _absorb(draft, previous, following, max_chars):
			merged.append(draft)
	return merged

def _absorb(draft: ChunkDraft, previous: ChunkDraft | None, following: ChunkDraft | None, max_chars: int) -> bool:
	"""Merge a fragment into a neighbour in FR-406 order. 按 FR-406 的顺序并入相邻段。"""
	def fits(other: ChunkDraft | None) -> bool:
		if other is None or len(draft.text) + 2 + len(other.text) > max_chars:
			return False
		# Never merge across sheets or location kinds. 不跨工作表或位置类型合并。
		return (other.location.kind, other.location.sheet) == (draft.location.kind, draft.location.sheet)

	if previous is not None and previous.title_path == draft.title_path and fits(previous):
		_append(previous, draft)
		return True
	if following is not None and fits(following) and following.title_path[: len(draft.title_path)] == draft.title_path:
		_prepend(following, draft)
		return True
	if previous is not None and _same_parent(previous, draft) and fits(previous):
		_append(previous, draft)
		return True
	if following is not None and _same_parent(following, draft) and fits(following):
		_prepend(following, draft)
		return True
	return False

def _append(target: ChunkDraft, extra: ChunkDraft) -> None:
	target.text = f'{target.text}\n\n{extra.text}'
	target.location = target.location.union(extra.location)

def _prepend(target: ChunkDraft, extra: ChunkDraft) -> None:
	target.text = f'{extra.text}\n\n{target.text}'
	target.location = target.location.union(extra.location)

def _same_parent(left: ChunkDraft, right: ChunkDraft) -> bool:
	return left.title_path[:-1] == right.title_path[:-1]

def _finalize(drafts: list[ChunkDraft], doc_id: str, doc_title: str) -> None:
	seen: dict[tuple[tuple[str, ...], str], int] = {}
	for ordinal, draft in enumerate(drafts):
		key = (tuple(draft.title_path), draft.text)
		count = seen.get(key, 0)
		seen[key] = count + 1
		digest = hashlib.sha1(f'{doc_id}\n{" > ".join(draft.title_path)}\n{draft.text}\n{count}'.encode()).hexdigest()
		draft.ordinal = ordinal
		draft.chunk_id = digest[:16]
		draft.index_text = f'{doc_title}\n{" > ".join(draft.title_path)}\n{draft.text}'
