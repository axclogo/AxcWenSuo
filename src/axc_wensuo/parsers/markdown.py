"""Markdown 解析（FR-301）。

按行扫描，保留标题层级、段落、列表、代码块（代码块内的 ``#`` 不当标题）和管道表格，并记录
每个块在原文中的字符偏移与行号，供切段与 ``location`` 使用。
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from axc_wensuo.parsers.base import (
	Block,
	ParsedDocument,
	Table,
	clean_heading,
	document_title,
	normalize_meta_list,
)

HEADING_RE = re.compile(r'^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$')
FENCE_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
LIST_RE = re.compile(r'^ {0,3}(?:[-*+]|\d{1,9}[.)])[ \t]+')
SEPARATOR_CELL_RE = re.compile(r'^:?-{1,}:?$')
META_KEYS = ('title', 'tags', 'aliases', 'scope')


def parse_markdown(doc_id: str, text: str) -> ParsedDocument:
	lines = text.splitlines(keepends=True)
	offsets = _offsets(lines)
	meta, body_start, warnings = _front_matter(lines)
	blocks = _parse_blocks(text, lines, offsets, body_start)
	scope_hint = meta.get('scope')
	return ParsedDocument(
		doc_id=doc_id,
		doc_title=document_title(meta.get('title'), blocks, doc_id),
		text=text,
		blocks=blocks,
		meta=meta,
		tags=normalize_meta_list(meta.get('tags')),
		aliases=normalize_meta_list(meta.get('aliases')),
		scope_hint=scope_hint.strip() if isinstance(scope_hint, str) and scope_hint.strip() else None,
		format='markdown',
		warnings=warnings,
	)


def _offsets(lines: list[str]) -> list[int]:
	offsets: list[int] = []
	position = 0
	for line in lines:
		offsets.append(position)
		position += len(line)
	offsets.append(position)
	return offsets


def _line_end(offsets: list[int], lines: list[str], index: int) -> int:
	return offsets[index] + len(lines[index].rstrip('\r\n'))


def _front_matter(lines: list[str]) -> tuple[dict[str, Any], int, list[str]]:
	if not lines or lines[0].strip() != '---':
		return {}, 0, []
	end: int | None = None
	for index in range(1, len(lines)):
		if lines[index].strip() in ('---', '...'):
			end = index
			break
	if end is None:
		return {}, 0, ['文件头 YAML 未闭合，按正文处理']
	raw = ''.join(lines[1:end])
	try:
		data = yaml.safe_load(raw)
	except yaml.YAMLError:
		return {}, 0, ['文件头 YAML 解析失败，按正文处理']
	if data is None:
		data = {}
	if not isinstance(data, dict):
		return {}, 0, ['文件头 YAML 不是映射，按正文处理']
	meta = {key: data[key] for key in META_KEYS if key in data}
	return meta, end + 1, []


def _parse_blocks(text: str, lines: list[str], offsets: list[int], start: int) -> list[Block]:
	blocks: list[Block] = []
	index = start
	total = len(lines)
	while index < total:
		line = lines[index]
		if not line.strip():
			index += 1
			continue
		heading = HEADING_RE.match(line.rstrip('\r\n'))
		if heading is not None:
			blocks.append(
				Block(
					kind='heading',
					start=offsets[index],
					end=_line_end(offsets, lines, index),
					text=text[offsets[index] : _line_end(offsets, lines, index)],
					line_start=index + 1,
					line_end=index + 1,
					level=len(heading.group(1)),
					label=clean_heading(heading.group(2)),
				)
			)
			index += 1
			continue
		fence = FENCE_RE.match(line.rstrip('\r\n'))
		if fence is not None:
			end = _fence_end(lines, index, fence.group(1))
			blocks.append(_block(kind='code', text=text, lines=lines, offsets=offsets, start=index, end=end))
			index = end + 1
			continue
		if _is_table_start(lines, index):
			row_lines, rows, end = _parse_table(lines, index)
			table = Table(rows=rows, header_rows=1, row_lines=row_lines)
			blocks.append(
				Block(
					kind='table',
					start=offsets[index],
					end=_line_end(offsets, lines, end),
					text='',
					line_start=index + 1,
					line_end=end + 1,
					table=table,
				)
			)
			index = end + 1
			continue
		if LIST_RE.match(line) is not None:
			end = _list_end(lines, index)
			blocks.append(_block(kind='list', text=text, lines=lines, offsets=offsets, start=index, end=end))
			index = end + 1
			continue
		end = _paragraph_end(lines, index)
		blocks.append(_block(kind='paragraph', text=text, lines=lines, offsets=offsets, start=index, end=end))
		index = end + 1
	return blocks


def _block(*, kind: str, text: str, lines: list[str], offsets: list[int], start: int, end: int) -> Block:
	return Block(
		kind=kind,
		start=offsets[start],
		end=_line_end(offsets, lines, end),
		text=text[offsets[start] : _line_end(offsets, lines, end)],
		line_start=start + 1,
		line_end=end + 1,
	)


def _fence_end(lines: list[str], start: int, marker: str) -> int:
	char = marker[0]
	length = len(marker)
	for index in range(start + 1, len(lines)):
		stripped = lines[index].strip()
		if stripped and set(stripped) == {char} and len(stripped) >= length:
			return index
	return len(lines) - 1


def _paragraph_end(lines: list[str], start: int) -> int:
	index = start + 1
	while index < len(lines):
		line = lines[index]
		if not line.strip():
			break
		if HEADING_RE.match(line.rstrip('\r\n')) is not None:
			break
		if FENCE_RE.match(line.rstrip('\r\n')) is not None:
			break
		if _is_table_start(lines, index):
			break
		if LIST_RE.match(line) is not None:
			break
		index += 1
	return index - 1


def _list_end(lines: list[str], start: int) -> int:
	index = start + 1
	while index < len(lines):
		line = lines[index]
		if not line.strip():
			break
		if LIST_RE.match(line) is not None:
			break
		if HEADING_RE.match(line.rstrip('\r\n')) is not None:
			break
		if FENCE_RE.match(line.rstrip('\r\n')) is not None:
			break
		if _is_table_start(lines, index):
			break
		if not line.startswith((' ', '\t')):
			break
		index += 1
	return index - 1


def _is_table_start(lines: list[str], index: int) -> bool:
	if index + 1 >= len(lines):
		return False
	if '|' not in lines[index] or not lines[index].strip():
		return False
	return _is_separator(lines[index + 1])


def _is_separator(line: str) -> bool:
	stripped = line.strip()
	if '|' not in stripped:
		return False
	cells = _split_row(stripped)
	if len(cells) < 2:
		return False
	return all(SEPARATOR_CELL_RE.match(cell.strip()) for cell in cells)


def _split_row(line: str) -> list[str]:
	stripped = line.strip()
	if stripped.startswith('|'):
		stripped = stripped[1:]
	if stripped.endswith('|') and not stripped.endswith('\\|'):
		stripped = stripped[:-1]
	cells: list[str] = []
	buffer: list[str] = []
	index = 0
	while index < len(stripped):
		char = stripped[index]
		if char == '\\' and index + 1 < len(stripped) and stripped[index + 1] == '|':
			buffer.append('|')
			index += 2
			continue
		if char == '|':
			cells.append(''.join(buffer).strip())
			buffer = []
			index += 1
			continue
		buffer.append(char)
		index += 1
	cells.append(''.join(buffer).strip())
	return cells


def _parse_table(lines: list[str], start: int) -> tuple[list[int], list[list[str]], int]:
	rows: list[list[str]] = []
	row_lines: list[int | None] = []
	index = start
	end = start
	while index < len(lines):
		line = lines[index]
		if not line.strip() or '|' not in line:
			break
		if _is_separator(line):
			index += 1
			end = index - 1
			continue
		rows.append(_split_row(line))
		row_lines.append(index + 1)
		end = index
		index += 1
	return row_lines, rows, end
