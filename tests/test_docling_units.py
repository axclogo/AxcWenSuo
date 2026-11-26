"""Docling adapter unit tests. Docling 适配层单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

from axc_wensuo.chunker import chunk_document
from axc_wensuo.parsers.docling import _build


def _label(value: str):
	return SimpleNamespace(value=value)

def _prov(*pages: int):
	return [SimpleNamespace(page_no=page) for page in pages]

def _text(value: str, label: str = 'text', pages=(1,)):
	return SimpleNamespace(text=value, label=_label(label), prov=_prov(*pages))

def _heading(value: str, pages=(1,)):
	return _text(value, 'section_header', pages)

def _cell(value: str):
	return SimpleNamespace(text=value)

def _table(rows: list[list[str]], pages=(2,)):
	grid = [[_cell(value) for value in row] for row in rows]
	return SimpleNamespace(label=_label('table'), prov=_prov(*pages), data=SimpleNamespace(grid=grid))

class _FakeDocument:
	def __init__(self, items):
		self._items = items

	def iterate_items(self, with_groups: bool = False):
		return iter(self._items)

def test_pdf_page_locations():
	document = _FakeDocument(
		[
			(_heading('巡检报告', pages=(1,)), 0),
			(_text('第一页内容。', pages=(1,)), 1),
			(_table([['项目', '状态'], ['磁盘', '正常']], pages=(2,)), 1),
			(_text('结论。', pages=(3,)), 1),
		]
	)
	parsed = _build('a.pdf', '.pdf', document)
	assert parsed.blocks[0].kind == 'heading'
	assert parsed.blocks[2].location.kind == 'page'
	assert parsed.blocks[2].location.start == 2
	chunks = chunk_document(parsed, scope='_root', doc_title=parsed.doc_title)
	assert any(chunk.location.kind == 'page' for chunk in chunks)

def test_xlsx_sheet_and_rows():
	sheet_group = SimpleNamespace(label=_label('sheet'), name='成本')
	document = _FakeDocument(
		[
			(sheet_group, 0),
			(_table([['科目', '金额'], ['人力', '5000'], ['设备', '2000']]), 1),
		]
	)
	parsed = _build('a.xlsx', '.xlsx', document)
	table_block = next(block for block in parsed.blocks if block.kind == 'table')
	assert table_block.location.kind == 'sheet'
	assert table_block.location.sheet == '成本'
	assert table_block.location.row_start == 2
	chunks = chunk_document(parsed, scope='_root', doc_title=parsed.doc_title)
	chunk = next(chunk for chunk in chunks if chunk.is_table)
	assert chunk.location.kind == 'sheet'
	assert chunk.location.sheet == '成本'
	assert chunk.location.row_start == 2
	assert chunk.location.row_end == 3

def test_pptx_slide_and_docx_section():
	pptx = _FakeDocument(
		[
			(_heading('第一页', pages=(1,)), 0),
			(_text('幻灯片内容。', pages=(1,)), 1),
		]
	)
	parsed = _build('a.pptx', '.pptx', pptx)
	assert all(block.location.kind == 'slide' for block in parsed.blocks)

	docx = _FakeDocument(
		[
			(_heading('年度总结', pages=(1,)), 0),
			(_text('正文。', pages=(1,)), 1),
		]
	)
	parsed = _build('a.docx', '.docx', docx)
	assert parsed.blocks[0].location.kind == 'section'
	chunks = chunk_document(parsed, scope='_root', doc_title=parsed.doc_title)
	assert chunks[0].location.kind == 'section'
