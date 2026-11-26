"""需要 docs 扩展（Docling）的验收测试（A-13）。未安装扩展时跳过。

Docling 首次解析 PDF 需要下载版面/表格模型。若模型不可用（离线或网络受限），PDF 用例会跳过并
在输出中说明；Office/HTML 用例不依赖这些模型。
"""

from __future__ import annotations

import pytest
from helpers import yaml_config

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

		xlsx_hits = kb.search('人力', sync=False).results
		hit = next(hit for hit in xlsx_hits if hit.doc_id == '数据/财务数据.xlsx')
		assert hit.location.kind == 'sheet'
		assert hit.location.sheet == '成本'
		assert hit.location.row_start == 2
		assert hit.location.row_end == 3

		docx_hits = kb.search('目标完成情况', sync=False).results
		assert docx_hits
		assert any(hit.title_path[-1:] == ['目标完成情况'] for hit in docx_hits)

		pptx_hits = kb.search('里程碑', sync=False).results
		assert pptx_hits
		assert any(hit.doc_id == '运维/项目汇报.pptx' and hit.location.kind == 'slide' for hit in pptx_hits)
	finally:
		kb.close()


def test_pdf_page_locations(corpus_copy):
	kb = _open(corpus_copy)
	try:
		report = kb.sync()
		pdf_failures = [entry for entry in kb.doctor().failed if entry.doc_id == '数据/巡检报告.pdf']
		if pdf_failures and any(
			keyword in pdf_failures[0].reason.lower() for keyword in ('hub', 'timeout', 'model', 'connect')
		):
			pytest.skip(f'Docling 模型不可用，跳过 PDF 用例：{pdf_failures[0].reason}')
		assert '数据/巡检报告.pdf' in report.added
		hits = kb.search('ZEPHYR CODE', sync=False).results
		assert hits
		pdf_hits = [hit for hit in hits if hit.doc_id == '数据/巡检报告.pdf']
		assert pdf_hits
		assert all(hit.location.kind == 'page' for hit in pdf_hits)
		assert any(hit.location.start is not None and hit.location.end is not None for hit in pdf_hits)
	finally:
		kb.close()
