"""PDF / Word / PowerPoint / Excel / HTML 解析（FR-304、FR-305），依赖可选 ``docs`` 扩展 Docling。

解析结果按「文件 SHA-256 + Docling 版本」缓存到 ``<index_dir>/parsed/``，切段配置变化时不重新解析。
Docling 不可用时由同步层记为“缺少 docs 扩展”跳过，不在这里报致命错误。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

from axc_wensuo.errors import WenSuoRuntimeError
from axc_wensuo.models import Location
from axc_wensuo.parsers.base import Block, ParsedDocument, ParseError, Table, document_title, render_table

PARSER_NAME = 'docling'
_CONVERTER: Any = None
_SAFE_VERSION_RE = re.compile(r'[^0-9A-Za-z_.\-]+')


def docling_available() -> bool:
	return importlib.util.find_spec('docling') is not None


def docling_version() -> str:
	if not docling_available():
		return 'missing'
	try:
		return importlib.metadata.version('docling')
	except Exception:  # pragma: no cover - 仅防御异常分发元数据
		return 'unknown'


def parser_version() -> str:
	return f'{PARSER_NAME}:{docling_version()}'


def parse_docling(doc_id: str, path: Path, index_dir: Path) -> ParsedDocument:
	if not docling_available():
		raise WenSuoRuntimeError('缺少 docs 扩展：请安装 docling（pip install "axc-wensuo[docs]"）后重试')
	digest = _sha256(path)
	cache_file = parsed_cache_path(index_dir, digest, docling_version())
	cache_file.parent.mkdir(parents=True, exist_ok=True)
	if cache_file.is_file():
		try:
			data = json.loads(cache_file.read_text(encoding='utf-8'))
			return _from_cache(doc_id, data)
		except (OSError, ValueError, KeyError):
			pass
	document = _convert(path)
	parsed = _build(doc_id, path.suffix.lower(), document)
	try:
		cache_file.write_text(json.dumps(_to_cache(parsed), ensure_ascii=False), encoding='utf-8')
	except OSError:
		pass
	return parsed


def parsed_cache_path(index_dir: str | Path, sha256: str, version: str) -> Path:
	return Path(index_dir) / 'parsed' / f'{sha256}-{_SAFE_VERSION_RE.sub("_", version)}.json'


def remove_parsed_cache(index_dir: str | Path, sha256: str, parser_version: str) -> bool:
	"""文件被删除时一并删除 Docling 解析缓存（FR-203）。"""
	if not parser_version.startswith(f'{PARSER_NAME}:'):
		return False
	version = parser_version.split(':', 1)[1]
	path = parsed_cache_path(index_dir, sha256, version)
	try:
		path.unlink()
	except FileNotFoundError:
		return False
	return True


def _sha256(path: Path) -> str:
	digest = hashlib.sha256()
	with path.open('rb') as handle:
		for chunk in iter(lambda: handle.read(1024 * 1024), b''):
			digest.update(chunk)
	return digest.hexdigest()


def _convert(path: Path) -> Any:
	global _CONVERTER
	if _CONVERTER is None:
		from docling.document_converter import DocumentConverter

		_CONVERTER = DocumentConverter()
	try:
		result = _CONVERTER.convert(str(path))
	except Exception as exc:
		raise ParseError(f'Docling 解析失败：{exc}') from exc
	return getattr(result, 'document', result)


def _build(doc_id: str, extension: str, document: Any) -> ParsedDocument:
	parts: list[str] = []
	blocks: list[Block] = []
	offset = 0

	def add(
		kind: str,
		text: str,
		*,
		level: int = 0,
		label: str | None = None,
		location: Location | None = None,
		table: Table | None = None,
	) -> None:
		nonlocal offset
		if not text.strip():
			return
		start = offset
		parts.append(text)
		offset += len(text) + 2
		blocks.append(
			Block(
				kind=kind,
				start=start,
				end=start + len(text),
				text=text,
				level=level,
				label=label,
				location=location or Location(kind='section'),
				table=table,
			)
		)

	items = document.iterate_items(with_groups=True) if hasattr(document, 'iterate_items') else []
	sheet: str | None = None
	sheet_level: int | None = None
	for item, level in items:
		raw_label = getattr(item, 'label', '')
		label = str(getattr(raw_label, 'value', raw_label)).lower()
		if label == 'sheet':
			sheet = str(getattr(item, 'name', '') or '')
			sheet_level = level
			continue
		if sheet_level is not None and level <= sheet_level:
			sheet = None
			sheet_level = None
		text = str(getattr(item, 'text', '') or '')
		if 'table' in label:
			rows = _table_rows(item, document)
			if not rows:
				continue
			location = _item_location(item, extension, sheet=sheet, rows=rows)
			table = Table(
				rows=rows,
				header_rows=1,
				sheet=sheet,
				row_start=location.row_start if location.kind == 'sheet' else None,
			)
			add('table', render_table(rows), location=location, table=table)
			continue
		if not text.strip():
			continue
		heading_level = int(level) if isinstance(level, int) and level > 0 else 1
		location = _item_location(item, extension, sheet=sheet)
		if 'section_header' in label or label in ('title', 'doc_title'):
			add(
				'heading',
				'#' * min(heading_level, 6) + ' ' + text.strip(),
				level=min(heading_level, 6),
				label=text.strip(),
				location=location,
			)
			continue
		kind = 'list' if 'list' in label else 'paragraph'
		add(kind, text, location=location)

	body = '\n\n'.join(parts)
	return ParsedDocument(
		doc_id=doc_id,
		doc_title=document_title(None, blocks, doc_id),
		text=body,
		blocks=blocks,
		format='docling',
	)


def _table_rows(item: Any, document: Any) -> list[list[str]]:
	exporter = getattr(item, 'export_to_dataframe', None)
	if callable(exporter):
		try:
			frame = exporter(document)
			rows = [[str(column) for column in frame.columns]]
			rows.extend([str(value) for value in row] for row in frame.itertuples(index=False, name=None))
			return rows
		except Exception:
			pass
	data = getattr(item, 'data', None)
	grid = getattr(data, 'grid', None) if data is not None else None
	if grid:
		rows = []
		for row in grid:
			rows.append([str(getattr(cell, 'text', '') or '') for cell in row])
		return rows
	return []


def _item_location(item: Any, extension: str, *, sheet: str | None = None, rows: Any = None) -> Location:
	provenance = getattr(item, 'prov', None) or []
	pages = [int(p.page_no) for p in provenance if getattr(p, 'page_no', None) is not None]
	if extension == '.pdf' and pages:
		return Location(kind='page', start=min(pages), end=max(pages))
	if extension == '.pptx' and pages:
		return Location(kind='slide', start=min(pages), end=max(pages))
	if extension == '.xlsx' and sheet:
		# Docling 把每个工作表当成一页；表头固定为第 1 行（需求 8.3）
		row_count = len(rows) if rows else 2
		return Location(kind='sheet', sheet=sheet, row_start=2, row_end=max(2, row_count))
	return Location(kind='section')


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
					'sheet': block.table.sheet,
					'row_start': block.table.row_start,
				},
			}
			for block in parsed.blocks
		],
	}


def _from_cache(doc_id: str, data: dict[str, Any]) -> ParsedDocument:
	blocks: list[Block] = []
	for raw in data.get('blocks', []):
		table_raw = raw.get('table')
		table = None
		if table_raw is not None:
			table = Table(
				rows=table_raw.get('rows', []),
				header_rows=table_raw.get('header_rows', 1),
				row_lines=table_raw.get('row_lines', []),
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
				location=Location.from_dict(raw.get('location') or {'kind': 'section'}),
				table=table,
			)
		)
	return ParsedDocument(
		doc_id=doc_id,
		doc_title=data.get('doc_title') or document_title(None, blocks, doc_id),
		text=data.get('text', ''),
		blocks=blocks,
		format='docling',
	)
