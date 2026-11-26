"""Docling integration tests (A-13). Docling 集成测试（A-13）。"""

from __future__ import annotations

import pytest
from helpers import search, yaml_config

from axc_wensuo import KnowledgeBase
from axc_wensuo.parsers import docs_extra_available

pytestmark = pytest.mark.skipif(not docs_extra_available(), reason='缺少 docs 扩展（docling）')


def _open(corpus_copy):
	(corpus_copy / 'wensuo.yaml').write_text(yaml_config(), encoding='utf-8')
	return KnowledgeBase(corpus_copy)

def test_office_and_html_locations(corpus_copy):
	(corpus_copy / '数据' / '巡检报告.pdf').unlink()
	kb = _open(corpus_copy)
	try:
		report = kb.sync()
		assert report.failed == []

		xlsx_hits = search(kb, '人力', sync=False).results
		hit = next(hit for hit in xlsx_hits if hit.doc_id == '数据/财务数据.xlsx')
		assert hit.location.kind == 'sheet'
		assert hit.location.sheet == '成本'
		assert hit.location.row_start == 2
		assert hit.location.row_end == 3

		docx_hits = search(kb, '目标完成情况', sync=False).results
		assert docx_hits
		assert any(hit.title_path[-1:] == ['目标完成情况'] for hit in docx_hits)

		pptx_hits = search(kb, '里程碑', sync=False).results
		assert pptx_hits
		assert any(hit.doc_id == '运维/项目汇报.pptx' and hit.location.kind == 'slide' for hit in pptx_hits)
	finally:
		kb.close()

@pytest.mark.skipif(
	__import__('os').environ.get('WENSUO_TEST_DOCLING_MODELS') != '1',
	reason='需要 Docling 版面/表格模型资产；设置 WENSUO_TEST_DOCLING_MODELS=1 后运行',
)
def test_pdf_page_locations(corpus_copy):
	kb = _open(corpus_copy)
	try:
		report = kb.sync()
		assert '数据/巡检报告.pdf' in report.added
		hits = search(kb, 'ZEPHYR CODE', sync=False).results
		assert hits
		pdf_hits = [hit for hit in hits if hit.doc_id == '数据/巡检报告.pdf']
		assert pdf_hits
		assert all(hit.location.kind == 'page' for hit in pdf_hits)
		assert any(hit.location.start is not None and hit.location.end is not None for hit in pdf_hits)
	finally:
		kb.close()
