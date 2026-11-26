from __future__ import annotations

import pytest

from axc_wensuo.errors import WenSuoRuntimeError
from axc_wensuo.parsers import find_section, parse_file, parse_markdown, parse_table, parse_text
from axc_wensuo.parsers.base import ParseError, render_table
from axc_wensuo.parsers.text import decode_text

MARKDOWN = """---
title: 自定义标题
tags: [alpha, beta]
aliases: deploy-guide
scope: 运维
---

# 一级标题

开头内容。

## 二级标题

段落一。

```python
# 代码块里的井号不是标题
print('x')
```

| 字段 | 说明 |
| --- | --- |
| user_id | 主键 |

### 三级标题

- 列表项一
- 列表项二

## 另一个二级标题

结尾。
"""


def test_markdown_front_matter_and_blocks():
	doc = parse_markdown('doc.md', MARKDOWN)
	assert doc.doc_title == '自定义标题'
	assert doc.tags == ['alpha', 'beta']
	assert doc.aliases == ['deploy-guide']
	assert doc.scope_hint == '运维'
	headings = [(block.level, block.label) for block in doc.blocks if block.kind == 'heading']
	assert headings == [(1, '一级标题'), (2, '二级标题'), (3, '三级标题'), (2, '另一个二级标题')]
	code = next(block for block in doc.blocks if block.kind == 'code')
	assert '# 代码块里的井号不是标题' in code.text
	assert code.line_start == 16
	table = next(block for block in doc.blocks if block.kind == 'table')
	assert table.table.rows == [['字段', '说明'], ['user_id', '主键']]
	assert table.table.row_lines == [21, 23]
	assert [block.kind for block in doc.blocks if block.kind == 'list'] == ['list', 'list']

def test_markdown_title_fallbacks():
	doc = parse_markdown('纪要.md', '# 会议纪要\n\n内容\n')
	assert doc.doc_title == '会议纪要'
	doc = parse_markdown('无标题文件.txt', '正文\n')
	assert doc.doc_title == '无标题文件'

def test_markdown_leading_heading_less_content():
	doc = parse_markdown('a.md', '前言段落\n\n# 标题\n\n正文\n')
	assert doc.blocks[0].kind == 'paragraph'
	assert doc.blocks[0].text == '前言段落'

def test_broken_front_matter_is_body():
	doc = parse_markdown('a.md', '---\ntitle: [\n---\n\n正文\n')
	assert doc.warnings
	assert doc.blocks[0].text.startswith('---')

def test_find_section():
	doc = parse_markdown('a.md', MARKDOWN)
	found = find_section(doc, ['一级标题', '二级标题'])
	assert found is not None
	start, end = found
	slice_text = doc.text[start:end]
	assert slice_text.startswith('## 二级标题')
	assert '### 三级标题' in slice_text
	assert '另一个二级标题' not in slice_text
	assert find_section(doc, ['一级标题', '不存在']) is None
	assert find_section(doc, ['二级标题']) is None
	assert find_section(doc, []) is None

def test_render_table_escapes():
	text = render_table([['a|b', 'x\ny'], ['c', 'd']])
	assert '| a\\|b | x y |' in text
	assert '| --- | --- |' in text

def test_text_paragraphs():
	doc = parse_text('a.txt', '第一段\n继续\n\n第二段\n')
	assert [block.text for block in doc.blocks] == ['第一段\n继续', '第二段']
	assert doc.blocks[0].line_start == 1
	assert doc.blocks[1].line_start == 4

def test_decode_gb18030():
	raw = '中文笔记'.encode('gb18030')
	text, encoding = decode_text(raw)
	assert text == '中文笔记'
	assert encoding == 'gb18030'

def test_decode_invalid():
	with pytest.raises(ParseError):
		decode_text(b'\xff\xff')

def test_csv_table():
	doc = parse_table('a.csv', '工号,姓名\nE001,张三\nE002,李四\n')
	assert doc.doc_title == 'a'
	assert len(doc.blocks) == 1
	table = doc.blocks[0].table
	assert table.rows[0] == ['工号', '姓名']
	assert table.row_lines == [1, 2, 3]
	assert '| E002 | 李四 |' in doc.text

def test_empty_csv():
	doc = parse_table('a.csv', '\n\n')
	assert doc.blocks == []
	assert doc.empty

def test_parse_file_dispatch_and_missing_docs(tmp_path):
	md = tmp_path / 'a.md'
	md.write_text('# 标题\n\n正文\n', encoding='utf-8')
	assert parse_file(md, 'a.md').doc_title == '标题'
	txt = tmp_path / 'a.txt'
	txt.write_bytes('中文'.encode('gb18030'))
	assert parse_file(txt, 'a.txt').text == '中文'
	unknown = tmp_path / 'a.xyz'
	unknown.write_text('x', encoding='utf-8')
	with pytest.raises(ParseError):
		parse_file(unknown, 'a.xyz')
	pdf = tmp_path / 'a.pdf'
	pdf.write_bytes(b'%PDF-1.4\n')
	if not __import__('axc_wensuo.parsers', fromlist=['docs_extra_available']).docs_extra_available():
		with pytest.raises(WenSuoRuntimeError, match='docs'):
			parse_file(pdf, 'a.pdf', index_dir=tmp_path / 'idx')

def test_unknown_front_matter_fields_ignored():
	doc = parse_markdown('a.md', '---\ntitle: T\nunknown: 1\n---\n\n正文\n')
	assert doc.doc_title == 'T'
	assert 'unknown' not in doc.meta

def test_parsed_cache_paths(tmp_path):
	from axc_wensuo.parsers.docling import parsed_cache_path, remove_parsed_cache

	path = parsed_cache_path(tmp_path, 'abc123', '2.5.0')
	assert path == tmp_path / 'parsed' / 'abc123-2.5.0.json'
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text('{}', encoding='utf-8')
	assert remove_parsed_cache(tmp_path, 'abc123', 'docling:2.5.0') is True
	assert not path.exists()
	assert remove_parsed_cache(tmp_path, 'abc123', 'docling:2.5.0') is False
	assert remove_parsed_cache(tmp_path, 'abc123', 'markdown:1') is False
