"""解析器注册与文件入口。

核心格式（Markdown、TXT、CSV、TSV）随核心包提供；PDF、DOCX、PPTX、XLSX、HTML 需要 ``docs``
扩展（Docling）。扩展名不区分大小写。
"""

from __future__ import annotations

from pathlib import Path

from axc_wensuo.parsers.base import (
	Block,
	ParsedDocument,
	ParseError,
	Table,
	clean_heading,
	document_title,
	find_section,
	heading_chains,
	normalize_meta_list,
	render_table,
)
from axc_wensuo.parsers.docling import docling_available, parse_docling
from axc_wensuo.parsers.markdown import parse_markdown
from axc_wensuo.parsers.table import parse_table
from axc_wensuo.parsers.text import decode_text, parse_text

CORE_EXTENSIONS = frozenset({'.md', '.markdown', '.txt', '.csv', '.tsv'})
DOCS_EXTENSIONS = frozenset({'.pdf', '.docx', '.pptx', '.xlsx', '.html', '.htm'})
SUPPORTED_EXTENSIONS = CORE_EXTENSIONS | DOCS_EXTENSIONS

_MD_EXTS = frozenset({'.md', '.markdown'})
_TABLE_EXTS = {'.csv': ',', '.tsv': '\t'}


def docs_extra_available() -> bool:
	return docling_available()


def parser_version_for(extension: str) -> str:
	suffix = extension.lower()
	if suffix in _MD_EXTS:
		return 'markdown:1'
	if suffix == '.txt':
		return 'text:1'
	if suffix in _TABLE_EXTS:
		return 'table:1'
	if suffix in DOCS_EXTENSIONS:
		from axc_wensuo.parsers import docling as docling_parser

		return docling_parser.parser_version()
	return 'unknown:0'


def parse_file(path: str | Path, doc_id: str, *, index_dir: str | Path | None = None) -> ParsedDocument:
	file_path = Path(path)
	suffix = file_path.suffix.lower()
	try:
		data = file_path.read_bytes()
	except OSError as exc:
		raise ParseError(f'无法读取文件：{exc}') from exc
	if suffix in DOCS_EXTENSIONS:
		if index_dir is None:
			raise ParseError('解析该格式需要索引目录（用于解析缓存）')
		return parse_docling(doc_id, file_path, Path(index_dir))
	text, _encoding = decode_text(data, path=str(file_path))
	if suffix in _MD_EXTS:
		return parse_markdown(doc_id, text)
	if suffix == '.txt':
		return parse_text(doc_id, text)
	if suffix in _TABLE_EXTS:
		return parse_table(doc_id, text, delimiter=_TABLE_EXTS[suffix])
	raise ParseError(f'不支持的扩展名：{suffix}')


__all__ = [
	'Block',
	'CORE_EXTENSIONS',
	'DOCS_EXTENSIONS',
	'SUPPORTED_EXTENSIONS',
	'ParsedDocument',
	'ParseError',
	'Table',
	'clean_heading',
	'decode_text',
	'docs_extra_available',
	'document_title',
	'find_section',
	'heading_chains',
	'normalize_meta_list',
	'parse_file',
	'parser_version_for',
	'render_table',
]
