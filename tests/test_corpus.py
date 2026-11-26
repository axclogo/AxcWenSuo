"""Corpus structure checks. 公开语料结构检查。"""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).parent / 'fixtures' / 'corpus'


def test_corpus_layout():
	required = {
		'运维/部署手册.md',
		'运维/代码示例.md',
		'运维/笔记.txt',
		'数据/设备台账.md',
		'数据/员工.csv',
		'数据/巡检报告.pdf',
		'数据/年度总结.docx',
		'数据/财务数据.xlsx',
		'运维/项目汇报.pptx',
		'接口说明.html',
	}
	present = {path.relative_to(FIXTURES).as_posix() for path in FIXTURES.rglob('*') if path.is_file()}
	assert required <= present

def test_corpus_text_features():
	guide = (FIXTURES / '运维' / '部署手册.md').read_text(encoding='utf-8')
	assert guide.startswith('---')
	assert '## 环境变量' in guide
	assert '| 步骤 |' in guide

	code = (FIXTURES / '运维' / '代码示例.md').read_text(encoding='utf-8')
	assert '```python' in code
	assert '# 这是代码块里的注释行' in code

	table = (FIXTURES / '数据' / '设备台账.md').read_text(encoding='utf-8')
	rows = [line for line in table.splitlines() if line.startswith('| ') and '---' not in line]
	assert len(rows) >= 100

	notes = (FIXTURES / '运维' / '笔记.txt').read_bytes()
	assert notes.decode('gb18030')

	csv_text = (FIXTURES / '数据' / '员工.csv').read_text(encoding='utf-8')
	assert csv_text.splitlines()[0] == '工号,姓名,部门,权限'

def test_corpus_binary_formats():
	pdf = (FIXTURES / '数据' / '巡检报告.pdf').read_bytes()
	assert pdf.startswith(b'%PDF')
	pages = pdf.count(b'/Type /Page') - pdf.count(b'/Type /Pages')
	assert pages >= 3
	for name in ('数据/年度总结.docx', '数据/财务数据.xlsx', '运维/项目汇报.pptx'):
		path = FIXTURES / name
		assert path.is_file()
		assert path.read_bytes().startswith(b'PK')
