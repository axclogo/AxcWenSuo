"""Docling adapter for PDF, Office, and HTML. PDF/Office/HTML 的 Docling 适配层。"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from axc_wensuo.errors import WenSuoRuntimeError
from axc_wensuo.hashing import file_sha256
from axc_wensuo.models import Location
from axc_wensuo.parsers.base import Block, ParsedDocument, ParseError, Table, document_title, render_table

PARSER_NAME = 'docling'
ADAPTER_VERSION = 2  # Bump when the adapter output changes. 适配层输出变化时加 1。
_CONVERTER: Any = None
_SAFE_VERSION_RE = re.compile(r'[^0-9A-Za-z_.\-]+')


def docling_available() -> bool:
	return importlib.util.find_spec('docling') is not None

def docling_version() -> str:
	"""Return the installed Docling version. 返回 Docling 版本。"""
	try:
		return importlib.metadata.version('docling')
	except importlib.metadata.PackageNotFoundError as exc:
		raise WenSuoRuntimeError('Docling 已安装但缺少包元数据；请重新安装 docs 扩展') from exc

def cache_version() -> str:
	"""Docling version plus adapter version. Docling 版本加适配层版本。"""
	return f'{docling_version()}+a{ADAPTER_VERSION}'

def parser_version() -> str:
	return f'{PARSER_NAME}:{cache_version()}'

def parsed_cache_path(index_dir: str | Path, sha256: str, version: str) -> Path:
	return Path(index_dir) / 'parsed' / f'{sha256}-{_SAFE_VERSION_RE.sub("_", version)}.json'

def remove_parsed_cache(index_dir: str | Path, sha256: str, parser_version: str) -> bool:
	"""Remove the cache for a deleted file. 删除文件时清理解析缓存。"""
	if not parser_version.startswith(f'{PARSER_NAME}:'):
		return False
	path = parsed_cache_path(index_dir, sha256, parser_version.split(':', 1)[1])
	try:
		path.unlink()
	except FileNotFoundError:
		return False
	return True

def parse_docling(doc_id: str, path: Path, index_dir: Path) -> ParsedDocument:
	"""Parse one file through Docling. 通过 Docling 解析单个文件。"""
	if not docling_available():
		raise WenSuoRuntimeError('缺少 docs 扩展：请安装 docling（pip install "axc-wensuo[docs]"）后重试')
	digest = file_sha256(path)
	cache_file = parsed_cache_path(index_dir, digest, cache_version())
	if cache_file.is_file():
		return _read_cache(cache_file, doc_id)
	document = _convert(path)
	parsed = _build(doc_id, path.suffix.lower(), document)
	_write_cache(cache_file, parsed)
	return parsed

def _convert(path: Path) -> Any:
	global _CONVERTER
	if _CONVERTER is None:
		from docling.document_converter import DocumentConverter

		_CONVERTER = DocumentConverter()
	from docling.exceptions import ConversionError

	try:
		result = _CONVERTER.convert(str(path))
	except ConversionError as exc:
		raise ParseError(f'Docling 解析失败：{exc}') from exc
	return result.document

def _build(doc_id: str, extension: str, document: Any) -> ParsedDocument:
	collector = _BlockCollector(extension)
	sheet: str | None = None
	sheet_level: int | None = None
	for item, level in document.iterate_items(with_groups=True):
		label = _label(item)
		if label == 'sheet':
			sheet, sheet_level = str(getattr(item, 'name', '') or ''), level
			continue
		if sheet_level is not None and level <= sheet_level:
			sheet, sheet_level = None, None
		if 'table' in label:
			collector.add_table(item, sheet)
			continue
		text = str(getattr(item, 'text', '') or '')
		if not text.strip():
			continue
		if 'section_header' in label or label in ('title', 'doc_title'):
			collector.add_heading(item, text, sheet)
		else:
			collector.add_text('list' if 'list' in label else 'paragraph', text, item, sheet)
	return collector.document(doc_id)

def _label(item: Any) -> str:
	raw = getattr(item, 'label', '')
	return str(getattr(raw, 'value', raw)).lower()

@dataclass(frozen=True)
class _BlockMeta:
	location: Location
	level: int = 0
	label: str | None = None
	table: Table | None = None

class _BlockCollector:
	"""Accumulate blocks and markdown text. 累积块与 Markdown 文本。"""

	def __init__(self, extension: str) -> None:
		self.extension = extension
		self.parts: list[str] = []
		self.blocks: list[Block] = []
		self.offset = 0

	def add_heading(self, item: Any, text: str, sheet: str | None) -> None:
		# Heading depth is the item's own level, not its tree depth. 标题级别取条目自带的 level，不是树深度。
		level = getattr(item, 'level', None)
		heading_level = min(level, 6) if isinstance(level, int) and level > 0 else 1
		location = _item_location(item, self.extension, sheet=sheet)
		self._append(
			'heading',
			f'{"#" * heading_level} {text.strip()}',
			_BlockMeta(location, level=heading_level, label=text.strip()),
		)

	def add_text(self, kind: str, text: str, item: Any, sheet: str | None) -> None:
		self._append(kind, text, _BlockMeta(_item_location(item, self.extension, sheet=sheet)))

	def add_table(self, item: Any, sheet: str | None) -> None:
		rows = _table_rows(item)
		location = _item_location(item, self.extension, sheet=sheet, rows=rows)
		table = Table(rows=rows, header_rows=1, sheet=sheet, row_start=location.row_start)
		self._append('table', render_table(rows), _BlockMeta(location, table=table))

	def _append(self, kind: str, text: str, meta: _BlockMeta) -> None:
		if not text.strip():
			return
		start = self.offset
		self.parts.append(text)
		self.offset += len(text) + 2
		self.blocks.append(
			Block(
				kind=kind,
				start=start,
				end=start + len(text),
				text=text,
				level=meta.level,
				label=meta.label,
				location=meta.location,
				table=meta.table,
			)
		)

	def document(self, doc_id: str) -> ParsedDocument:
		return ParsedDocument(
			doc_id=doc_id,
			doc_title=document_title(None, self.blocks, doc_id),
			text='\n\n'.join(self.parts),
			blocks=self.blocks,
			format='docling',
		)

def _table_rows(item: Any) -> list[list[str]]:
	data = getattr(item, 'data', None)
	grid = getattr(data, 'grid', None) if data is not None else None
	if not grid:
		raise ParseError('Docling 表格缺少单元格数据')
	return [[str(getattr(cell, 'text', '') or '') for cell in row] for row in grid]

def _item_location(item: Any, extension: str, *, sheet: str | None = None, rows: Any = None) -> Location:
	provenance = getattr(item, 'prov', None) or []
	pages = [int(p.page_no) for p in provenance if getattr(p, 'page_no', None) is not None]
	if extension == '.pdf' and pages:
		return Location(kind='page', start=min(pages), end=max(pages))
	if extension == '.pptx' and pages:
		return Location(kind='slide', start=min(pages), end=max(pages))
	if extension == '.xlsx' and sheet:
		row_end = max(2, len(rows)) if rows else 2
		return Location(kind='sheet', sheet=sheet, row_start=2, row_end=row_end)
	return Location(kind='section')

def _read_cache(cache_file: Path, doc_id: str) -> ParsedDocument:
	try:
		data = json.loads(cache_file.read_text(encoding='utf-8'))
		return _from_cache(doc_id, data)
	except (OSError, ValueError, KeyError, TypeError) as exc:
		raise WenSuoRuntimeError(f'解析缓存损坏：{cache_file}；请删除该文件后重试') from exc

def _write_cache(cache_file: Path, parsed: ParsedDocument) -> None:
	cache_file.parent.mkdir(parents=True, exist_ok=True)
	try:
		cache_file.write_text(json.dumps(_to_cache(parsed), ensure_ascii=False), encoding='utf-8')
	except OSError as exc:
		raise WenSuoRuntimeError(f'无法写入解析缓存 {cache_file}：{exc}') from exc

def _to_cache(parsed: ParsedDocument) -> dict[str, Any]:
	return {
		'doc_title': parsed.doc_title,
		'text': parsed.text,
		'blocks': [
			{
				'kind': block.kind,
				'start': block.start,
				'end': block.end,
				'text': block.text,
				'level': block.level,
				'label': block.label,
				'location': (block.location or Location(kind='section')).to_dict(),
				'table': None
				if block.table is None
				else {
					'rows': block.table.rows,
					'header_rows': block.table.header_rows,
					'row_lines': block.table.row_lines,
					'row_end_lines': block.table.row_end_lines,
					'sheet': block.table.sheet,
					'row_start': block.table.row_start,
				},
			}
			for block in parsed.blocks
		],
	}

def _from_cache(doc_id: str, data: dict[str, Any]) -> ParsedDocument:
	blocks: list[Block] = []
	for raw in data['blocks']:
		table_raw = raw.get('table')
		table = None
		if table_raw is not None:
			table = Table(
				rows=table_raw['rows'],
				header_rows=table_raw['header_rows'],
				row_lines=table_raw.get('row_lines', []),
				row_end_lines=table_raw.get('row_end_lines', []),
				sheet=table_raw.get('sheet'),
				row_start=table_raw.get('row_start'),
			)
		blocks.append(
			Block(
				kind=raw['kind'],
				start=raw['start'],
				end=raw['end'],
				text=raw['text'],
				level=raw.get('level', 0),
				label=raw.get('label'),
				location=Location.from_dict(raw['location']),
				table=table,
			)
		)
	return ParsedDocument(
		doc_id=doc_id,
		doc_title=data['doc_title'],
		text=data['text'],
		blocks=blocks,
		format='docling',
	)
