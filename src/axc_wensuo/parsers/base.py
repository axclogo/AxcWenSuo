"""Shared parser structures and helpers. 解析器公共结构与工具。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from axc_wensuo.models import Location

_OPENING_RE = re.compile(r'^#{1,6}(?:[ \t]+|$)')
_CLOSING_RE = re.compile(r'(?:^|[ \t]+)#+$')


class ParseError(Exception):
	"""One file failed to parse. 单个文件解析失败。"""

@dataclass
class Table:
	"""One table; first row is header. 一张表，第一行为表头。"""

	rows: list[list[str]]
	header_rows: int = 1
	row_lines: list[int | None] = field(default_factory=list)  # First line of each row. 每行的起始行号。
	row_end_lines: list[int | None] = field(default_factory=list)  # Row end line. 每行的结束行号。
	sheet: str | None = None
	row_start: int | None = None

@dataclass
class Block:
	"""Structural block with source offsets. 带原文偏移的结构块。"""

	kind: str  # Block kind. 块类型。
	start: int
	end: int
	text: str
	line_start: int | None = None
	line_end: int | None = None
	level: int = 0
	label: str | None = None
	table: Table | None = None
	location: Location | None = None

	def location_or_lines(self) -> Location:
		if self.location is not None:
			return self.location
		if self.line_start is not None and self.line_end is not None:
			return Location(kind='lines', start=self.line_start, end=self.line_end)
		return Location(kind='section')

@dataclass
class ParsedDocument:
	doc_id: str
	doc_title: str
	text: str
	blocks: list[Block]
	meta: dict[str, Any] = field(default_factory=dict)
	tags: list[str] = field(default_factory=list)
	aliases: list[str] = field(default_factory=list)
	scope_hint: str | None = None
	format: str = ''
	warnings: list[str] = field(default_factory=list)

	@property
	def empty(self) -> bool:
		return not any(block.text.strip() for block in self.blocks)

def clean_heading(text: str) -> str:
	"""Strip ATX marker and closing #s; keep C#. 去掉 ATX 记号与闭合 #，保留 C# 这类紧贴文字的 #。"""
	return _CLOSING_RE.sub('', _OPENING_RE.sub('', text.strip())).strip()

def heading_title(block: Block) -> str:
	"""Heading text of a heading block. 标题块的标题文本。"""
	return block.label if block.label is not None else clean_heading(block.text)

def document_title(meta_title: Any, blocks: list[Block], doc_id: str) -> str:
	"""Title precedence from FR-132. 标题优先级（FR-132）。"""
	if isinstance(meta_title, str) and meta_title.strip():
		return meta_title.strip()
	for block in blocks:
		if block.kind == 'heading' and block.level == 1:
			title = heading_title(block)
			if title:
				return title
	return Path(doc_id).stem

def normalize_meta_list(value: Any) -> list[str]:
	if isinstance(value, str):
		return [value] if value.strip() else []
	if isinstance(value, list):
		return [item.strip() for item in value if isinstance(item, str) and item.strip()]
	return []

def _normalize_title(value: str) -> str:
	return unicodedata.normalize('NFC', value).strip()

def find_section(doc: ParsedDocument, section: list[str]) -> tuple[int, int] | None:
	"""Find a heading chain prefix. 按标题链前缀查找章节。"""
	target = [_normalize_title(item) for item in section]
	if not target:
		return None
	stack: list[tuple[int, str]] = []
	for index, block in enumerate(doc.blocks):
		if block.kind != 'heading':
			continue
		level = block.level or 1
		while stack and stack[-1][0] >= level:
			stack.pop()
		stack.append((level, heading_title(block)))
		chain = [_normalize_title(title) for _, title in stack]
		if chain[: len(target)] != target:
			continue
		end = len(doc.text)
		for later in doc.blocks[index + 1 :]:
			if later.kind == 'heading' and (later.level or 1) <= level:
				end = later.start
				break
		return block.start, end
	return None

def render_table(rows: list[list[str]], header_rows: int = 1) -> str:
	"""Render a Markdown pipe table. 渲染 Markdown 管道表格。"""
	if not rows:
		return ''
	header_rows = max(1, min(header_rows, len(rows)))
	width = max(len(row) for row in rows)
	lines: list[str] = []
	for index, row in enumerate(rows):
		cells = list(row) + [''] * (width - len(row))
		lines.append('| ' + ' | '.join(_escape_cell(cell) for cell in cells) + ' |')
		if index == header_rows - 1:
			lines.append('| ' + ' | '.join('---' for _ in range(width)) + ' |')
	return '\n'.join(lines)

def _escape_cell(value: str) -> str:
	return str(value).replace('|', '\\|').replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ').strip()
