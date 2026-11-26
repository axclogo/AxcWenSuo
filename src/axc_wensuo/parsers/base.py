"""解析器的公共数据结构与工具。"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from axc_wensuo.models import Location


class ParseError(Exception):
	"""单个文件解析失败；同步层捕获后记入失败清单（FR-206）。"""


@dataclass
class Table:
	"""一张表。``rows`` 第一行是表头（``header_rows`` 行）。"""

	rows: list[list[str]]
	header_rows: int = 1
	row_lines: list[int | None] = field(default_factory=list)
	sheet: str | None = None
	row_start: int | None = None


@dataclass
class Block:
	"""解析出的一个结构块，带原文偏移。"""

	kind: str  # heading / paragraph / list / code / table
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
	"""去掉 Markdown 标题记号与首尾空白。"""
	return text.strip().lstrip('#').strip().rstrip('#').strip()


def document_title(meta_title: Any, blocks: list[Block], doc_id: str) -> str:
	"""FR-132：文件头 title > 第一个一级标题 > 文件名（去扩展名）。"""
	if isinstance(meta_title, str) and meta_title.strip():
		return meta_title.strip()
	for block in blocks:
		if block.kind == 'heading' and block.level == 1:
			title = clean_heading(block.label or block.text)
			if title:
				return title
	return Path(doc_id).stem


def normalize_meta_list(value: Any) -> list[str]:
	if isinstance(value, str):
		return [value] if value.strip() else []
	if isinstance(value, list):
		return [item.strip() for item in value if isinstance(item, str) and item.strip()]
	return []


def heading_chains(blocks: list[Block]) -> list[tuple[Block, list[str]]]:
	"""按文档顺序返回每个标题块的完整标题链。"""
	result: list[tuple[Block, list[str]]] = []
	stack: list[tuple[int, str]] = []
	for block in blocks:
		if block.kind != 'heading':
			continue
		level = block.level or 1
		while stack and stack[-1][0] >= level:
			stack.pop()
		stack.append((level, clean_heading(block.label or block.text)))
		result.append((block, [title for _, title in stack]))
	return result


def _normalize_title(value: str) -> str:
	return unicodedata.normalize('NFC', value).strip()


def find_section(doc: ParsedDocument, section: list[str]) -> tuple[int, int] | None:
	"""FR-307：按标题链前缀精确匹配，返回（起始偏移、结束偏移）。"""
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
		stack.append((level, clean_heading(block.label or block.text)))
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
	"""渲染 Markdown 管道表格：单元格内 ``|`` 转义、换行替换为空格（FR-405）。"""
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
