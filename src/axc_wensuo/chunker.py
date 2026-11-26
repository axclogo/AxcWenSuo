"""Chunking rules FR-401..FR-409. 切段规则（FR-401~FR-409）。"""

from __future__ import annotations

import bisect
import hashlib
from dataclasses import dataclass, field

from axc_wensuo.models import Location
from axc_wensuo.parsers.base import Block, ParsedDocument, clean_heading, render_table

CHUNKER_VERSION = 1
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

@dataclass
class _Unit:
	blocks: list[Block] = field(default_factory=list)
	title_path: list[str] = field(default_factory=list)

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
	for unit in _units(doc.blocks):
		drafts.extend(_chunk_unit(doc, unit, line_starts, max_chars))
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
			stack.append((level, clean_heading(block.label or block.text)))
			units.append(_Unit(blocks=[block], title_path=[title for _, title in stack]))
			continue
		if not units:
			units.append(_Unit())
		units[-1].blocks.append(block)
	return units

def _unit_text(doc: ParsedDocument, blocks: list[Block]) -> str:
	"""Prefer the original source slice. 优先取原文切片。"""
	return doc.text[blocks[0].start : blocks[-1].end]

def _union_location(blocks: list[Block]) -> Location:
	location = blocks[0].location_or_lines()
	for block in blocks[1:]:
		location = location.union(block.location_or_lines())
	return location

def _chunk_unit(doc: ParsedDocument, unit: _Unit, line_starts: list[int], max_chars: int) -> list[ChunkDraft]:
	blocks = unit.blocks
	if not blocks:
		return []
	has_table = any(block.kind == 'table' for block in blocks)
	unit_text = _unit_text(doc, blocks)
	if not has_table and len(unit_text) <= max_chars:
		return [ChunkDraft(text=unit_text, title_path=list(unit.title_path), location=_union_location(blocks))]

	drafts: list[ChunkDraft] = []
	segment: list[Block] = []
	for block in blocks:
		if block.kind == 'table':
			if segment:
				drafts.extend(_pack_blocks(doc, segment, unit.title_path, line_starts, max_chars))
				segment = []
			drafts.extend(_table_chunks(block, unit.title_path, max_chars))
		else:
			segment.append(block)
	if segment:
		drafts.extend(_pack_blocks(doc, segment, unit.title_path, line_starts, max_chars))
	return drafts

def _pack_blocks(
	doc: ParsedDocument,
	blocks: list[Block],
	title_path: list[str],
	line_starts: list[int],
	max_chars: int,
) -> list[ChunkDraft]:
	atoms = _atoms(doc, blocks, line_starts, max_chars)
	drafts: list[ChunkDraft] = []
	current: list[_Atom] = []
	for atom in atoms:
		if not current:
			if atom.end - atom.start > max_chars:
				drafts.append(ChunkDraft(text=atom.text, title_path=list(title_path), location=atom.location))
				continue
			current = [atom]
			continue
		if atom.end - current[0].start <= max_chars:
			current.append(atom)
			continue
		drafts.append(_draft_from_atoms(doc, current, title_path))
		if atom.end - atom.start > max_chars:
			drafts.append(ChunkDraft(text=atom.text, title_path=list(title_path), location=atom.location))
			current = []
			continue
		current = [atom]
	if current:
		drafts.append(_draft_from_atoms(doc, current, title_path))
	return drafts

def _draft_from_atoms(doc: ParsedDocument, atoms: list[_Atom], title_path: list[str]) -> ChunkDraft:
	text = doc.text[atoms[0].start : atoms[-1].end]
	location = atoms[0].location
	for atom in atoms[1:]:
		location = location.union(atom.location)
	return ChunkDraft(text=text, title_path=list(title_path), location=location)

def _atoms(doc: ParsedDocument, blocks: list[Block], line_starts: list[int], max_chars: int) -> list[_Atom]:
	atoms: list[_Atom] = []
	for block in blocks:
		if block.kind == 'table' and block.table is not None:
			atoms.append(
				_Atom(
					text=render_table(block.table.rows, block.table.header_rows),
					location=block.location_or_lines(),
					start=block.start,
					end=block.end,
				)
			)
			continue
		text = block.text
		if len(text) <= max_chars:
			atoms.append(_Atom(text=text, location=block.location_or_lines(), start=block.start, end=block.end))
			continue
		if block.kind == 'code' and len(text) <= 2 * max_chars:
			atoms.append(_Atom(text=text, location=block.location_or_lines(), start=block.start, end=block.end))
			continue
		for start, end in _split_spans(text, max_chars, code=block.kind == 'code'):
			piece = text[start:end]
			atoms.append(
				_Atom(
					text=piece,
					location=_piece_location(doc, block, start, end, line_starts),
					start=block.start + start,
					end=block.start + end,
				)
			)
	return atoms

def _split_spans(text: str, max_chars: int, *, code: bool = False) -> list[tuple[int, int]]:
	if code:
		return _pack_line_spans(text, max_chars)
	return _pack_sentence_spans(text, max_chars)

def _pack_sentence_spans(text: str, max_chars: int) -> list[tuple[int, int]]:
	spans: list[tuple[int, int]] = []
	start = 0
	for index, char in enumerate(text):
		if char in SENTENCE_CHARS or char == '\n':
			spans.append((start, index + 1))
			start = index + 1
	if start < len(text):
		spans.append((start, len(text)))
	pieces: list[tuple[int, int]] = []
	for span_start, span_end in spans:
		while span_end - span_start > max_chars:
			pieces.append((span_start, span_start + max_chars))
			span_start += max_chars
		if span_end > span_start:
			pieces.append((span_start, span_end))
	result: list[tuple[int, int]] = []
	current_start = 0
	current_end = 0
	for piece_start, piece_end in pieces:
		if current_end == 0:
			current_start, current_end = piece_start, piece_end
			continue
		if piece_end - current_start > max_chars:
			result.append((current_start, current_end))
			current_start, current_end = piece_start, piece_end
		else:
			current_end = piece_end
	if current_end > current_start:
		result.append((current_start, current_end))
	return result

def _pack_line_spans(text: str, max_chars: int) -> list[tuple[int, int]]:
	lines: list[tuple[int, int]] = []
	offset = 0
	for line in text.splitlines(keepends=True):
		start = offset
		end = offset + len(line)
		offset = end
		while end - start > max_chars:
			lines.append((start, start + max_chars))
			start += max_chars
		if end > start:
			lines.append((start, end))
	if offset < len(text):
		lines.append((offset, len(text)))
	result: list[tuple[int, int]] = []
	current_start = 0
	current_end = 0
	for piece_start, piece_end in lines:
		if current_end == 0:
			current_start, current_end = piece_start, piece_end
			continue
		if piece_end - current_start > max_chars:
			result.append((current_start, current_end))
			current_start, current_end = piece_start, piece_end
		else:
			current_end = piece_end
	if current_end > current_start:
		result.append((current_start, current_end))
	return result

def _piece_location(doc: ParsedDocument, block: Block, start: int, end: int, line_starts: list[int]) -> Location:
	if block.location is not None and block.line_start is None:
		return block.location
	if block.line_start is None or block.line_end is None:
		return Location(kind='section')
	start_line = _line_number(line_starts, block.start + start)
	end_line = _line_number(line_starts, block.start + max(start, end - 1))
	return Location(kind='lines', start=start_line, end=end_line)

def _table_chunks(block: Block, title_path: list[str], max_chars: int) -> list[ChunkDraft]:
	table = block.table
	if table is None or not table.rows:
		return []
	header_rows = max(1, min(table.header_rows, len(table.rows)))
	header = table.rows[:header_rows]
	data = table.rows[header_rows:]
	if not data:
		return [
			ChunkDraft(
				text=render_table(header, header_rows),
				title_path=list(title_path),
				location=block.location_or_lines(),
				is_table=True,
			)
		]
	drafts: list[ChunkDraft] = []
	start = 0
	while start < len(data):
		end = start
		while end < len(data):
			candidate = render_table(header + data[start : end + 1], header_rows)
			if end > start and len(candidate) > max_chars:
				break
			end += 1
			if len(candidate) > max_chars:
				break
		rows_slice = data[start:end]
		text = render_table(header + rows_slice, header_rows)
		location = _table_location(block, table, header_rows, start, end)
		drafts.append(ChunkDraft(text=text, title_path=list(title_path), location=location, is_table=True))
		start = end if end > start else start + 1
	return drafts

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
			last = table.row_lines[last_index]
			if first is not None and last is not None:
				return Location(kind='lines', start=min(first, last), end=max(first, last))
	return location

def _merge_short(drafts: list[ChunkDraft], min_chars: int, max_chars: int) -> list[ChunkDraft]:
	items: list[ChunkDraft | None] = list(drafts)
	index = 0
	while index < len(items):
		current = items[index]
		if current is None or current.is_table or len(current.text) >= min_chars:
			index += 1
			continue
		previous = items[index - 1] if index > 0 else None
		if (
			previous is not None
			and not previous.is_table
			and _same_parent(previous, current)
			and len(previous.text) + 2 + len(current.text) <= max_chars
		):
			previous.text = previous.text + '\n\n' + current.text
			previous.location = previous.location.union(current.location)
			items[index] = None
			index += 1
			continue
		next_index = index + 1
		while next_index < len(items) and items[next_index] is None:
			next_index += 1
		following = items[next_index] if next_index < len(items) else None
		if (
			following is not None
			and not following.is_table
			and _same_parent(following, current)
			and len(current.text) + 2 + len(following.text) <= max_chars
		):
			following.text = current.text + '\n\n' + following.text
			following.location = current.location.union(following.location)
			items[index] = None
		index += 1
	return [item for item in items if item is not None]

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
