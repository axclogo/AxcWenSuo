from __future__ import annotations

from axc_wensuo.chunker import chunk_document
from axc_wensuo.models import Location
from axc_wensuo.parsers import parse_markdown


def _chunks(text: str, *, max_chars: int = 1200, min_chars: int = 1):
	doc = parse_markdown('a.md', text)
	return chunk_document(doc, scope='_root', doc_title=doc.doc_title, max_chars=max_chars, min_chars=min_chars)

def test_heading_units_and_title_paths():
	text = '# A\n\na1\n\n## B\n\nb1\n\n段落\n\n# C\n\nc1\n'
	chunks = _chunks(text)
	assert [chunk.title_path for chunk in chunks] == [['A'], ['A', 'B'], ['C']]
	assert chunks[0].text == '# A\n\na1'
	assert chunks[1].text.startswith('## B')
	assert '段落' in chunks[1].text
	assert chunks[2].text == '# C\n\nc1'

def test_content_before_first_heading():
	chunks = _chunks('前言\n\n# 标题\n\n正文\n')
	assert chunks[0].title_path == []
	assert chunks[0].text == '前言'

def test_short_unit_single_chunk():
	text = '# 标题\n\n第一段\n\n第二段\n'
	chunks = _chunks(text, max_chars=1000)
	assert len(chunks) == 1
	assert chunks[0].text == text.rstrip('\n')

def test_long_unit_packs_paragraphs():
	paragraphs = [f'第{i}段' + '内容' * 10 for i in range(6)]
	text = '# 标题\n\n' + '\n\n'.join(paragraphs) + '\n'
	chunks = _chunks(text, max_chars=80)
	assert len(chunks) > 1
	assert chunks[0].text.startswith('# 标题\n\n第0段')
	for chunk in chunks:
		assert len(chunk.text) <= 80
		assert chunk.title_path == ['标题']
	for chunk in chunks[1:]:
		assert not chunk.text.startswith('# 标题')

def test_sentence_split_of_oversized_paragraph():
	paragraph = '这是一个很长的句子。' * 10
	chunks = _chunks(paragraph, max_chars=30)
	assert len(chunks) > 1
	for chunk in chunks:
		assert len(chunk.text) <= 30
	for chunk in chunks[:-1]:
		assert chunk.text[-1] in '。！？；.!?;'

def test_hard_cut_when_single_sentence_too_long():
	chunks = _chunks('一' * 100, max_chars=30)
	assert [len(chunk.text) for chunk in chunks] == [30, 30, 30, 10]

def test_code_block_not_split_under_twice_max():
	code = '```\n' + 'x = 1\n' * 3 + '```\n'
	chunks = _chunks(code, max_chars=20)
	assert len(chunks) == 1
	assert len(chunks[0].text) > 20

def test_code_block_split_over_twice_max():
	code = '```python\n' + ''.join(f'line_{index} = {index}\n' for index in range(30)) + '```\n'
	chunks = _chunks(code, max_chars=40)
	assert len(chunks) > 1
	for chunk in chunks:
		assert len(chunk.text) <= 40

def test_table_chunks_repeat_header_and_locations():
	rows = '\n'.join(f'| {index:03d} | 名称{index} |' for index in range(1, 21))
	text = f'# 表格\n\n| 编号 | 名称 |\n| --- | --- |\n{rows}\n'
	chunks = _chunks(text, max_chars=120)
	table_chunks = [chunk for chunk in chunks if chunk.text.startswith('| 编号')]
	assert len(table_chunks) > 1
	for chunk in table_chunks:
		assert chunk.text.splitlines()[0] == '| 编号 | 名称 |'
		assert chunk.text.splitlines()[1] == '| --- | --- |'
		assert chunk.location.kind == 'lines'
	assert table_chunks[0].location.start == 5
	assert table_chunks[0].location.end < table_chunks[-1].location.end

def test_short_chunks_merge_into_previous():
	text = '# A\n\n短一\n\n短二\n\n短三\n'
	chunks = _chunks(text, max_chars=200, min_chars=50)
	assert len(chunks) == 1
	assert chunks[0].text == '# A\n\n短一\n\n短二\n\n短三'
	assert chunks[0].location == Location(kind='lines', start=1, end=7)

def test_short_merge_respects_parent_scope():
	text = '# A\n\n## B\n\n短一\n\n# C\n\n## D\n\n短二\n'
	chunks = _chunks(text, max_chars=200, min_chars=50)
	# Heading units join their first child; A and C never merge. 标题单元并入首个子章节；A 与 C 不合并。
	assert [chunk.title_path for chunk in chunks] == [['A', 'B'], ['C', 'D']]
	assert [chunk.text for chunk in chunks] == ['# A\n\n## B\n\n短一', '# C\n\n## D\n\n短二']

def test_short_text_before_table_becomes_caption():
	text = '# A\n\n短文字\n\n| x | y |\n| --- | --- |\n| 1 | 2 |\n'
	chunks = _chunks(text, max_chars=500, min_chars=100)
	assert len(chunks) == 1
	assert chunks[0].is_table
	assert chunks[0].text == '# A\n\n短文字\n\n| x | y |\n| --- | --- |\n| 1 | 2 |'
	assert chunks[0].location == Location(kind='lines', start=1, end=7)

def test_chunk_ids_unique_for_duplicate_sections():
	text = '# A\n\n## 重复\n\n同样内容\n\n## 重复\n\n同样内容\n'
	chunks = _chunks(text, min_chars=1)
	duplicates = [chunk for chunk in chunks if chunk.title_path == ['A', '重复']]
	assert len(duplicates) == 2
	assert duplicates[0].chunk_id != duplicates[1].chunk_id
	assert duplicates[0].text == duplicates[1].text

def test_chunk_id_stable_when_other_section_changes():
	before = _chunks('# A\n\n内容一\n\n# B\n\n内容二\n')
	after = _chunks('# A\n\n内容一\n\n# B\n\n内容二修改\n')
	before_ids = {tuple(chunk.title_path): chunk.chunk_id for chunk in before}
	after_ids = {tuple(chunk.title_path): chunk.chunk_id for chunk in after}
	assert before_ids[('A',)] == after_ids[('A',)]
	assert before_ids[('B',)] != after_ids[('B',)]

def test_index_text_and_ordinals():
	chunks = _chunks('# 标题\n\n正文\n')
	assert [chunk.ordinal for chunk in chunks] == [0]
	assert chunks[0].index_text == '标题\n标题\n# 标题\n\n正文' or chunks[0].index_text.startswith('标题\n标题\n')
	doc_title = chunks[0].index_text.split('\n')[0]
	assert doc_title == '标题'

def test_ordinals_sequential_after_merge():
	text = '# A\n\n短\n\n# B\n\n' + '长内容' * 100 + '\n'
	chunks = _chunks(text, max_chars=100, min_chars=50)
	assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))

def test_sheet_table_location():
	from axc_wensuo.parsers.base import Block, ParsedDocument, Table

	table = Table(rows=[['a', 'b'], ['1', '2'], ['3', '4']], header_rows=1, sheet='Sheet1', row_start=1)
	doc = ParsedDocument(
		doc_id='a.xlsx',
		doc_title='表',
		text='| a | b |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |',
		blocks=[
			Block(
				kind='table',
				start=0,
				end=0,
				text='',
				table=table,
				location=Location(kind='sheet', sheet='Sheet1', row_start=1, row_end=3),
			)
		],
	)
	chunks = chunk_document(doc, scope='x', doc_title='表', max_chars=1000, min_chars=1)
	assert chunks[0].location == Location(kind='sheet', sheet='Sheet1', row_start=1, row_end=2)


def test_empty_parent_heading_joins_first_child():
	text = '# 用户表\n\n## 字段说明\n\n' + '字段内容。' * 30 + '\n\n## 枚举值\n\n' + '枚举内容。' * 30 + '\n'
	chunks = _chunks(text, min_chars=80)
	assert [chunk.title_path for chunk in chunks] == [['用户表', '字段说明'], ['用户表', '枚举值']]
	assert chunks[0].text.startswith('# 用户表\n\n## 字段说明\n\n字段内容')
	assert chunks[0].location.start == 1


def test_long_parent_intro_stays_separate():
	intro = '概述内容。' * 20
	text = f'# 用户表\n\n{intro}\n\n## 字段说明\n\n' + '字段内容。' * 30 + '\n'
	chunks = _chunks(text, min_chars=80)
	assert [chunk.title_path for chunk in chunks] == [['用户表'], ['用户表', '字段说明']]


def test_caption_and_header_repeat_in_every_table_chunk():
	rows = '\n'.join(f'| {index} | 说明{index} |' for index in range(40))
	text = f'## 枚举值\n\n**status 状态**：\n\n| 值 | 说明 |\n| --- | --- |\n{rows}\n'
	chunks = _chunks(text, max_chars=200, min_chars=80)
	assert len(chunks) > 1
	assert chunks[0].text.startswith('## 枚举值\n\n**status 状态**：\n\n| 值 | 说明 |\n| --- | --- |\n| 0 |')
	for chunk in chunks[1:]:
		assert chunk.text.startswith('**status 状态**：\n\n| 值 | 说明 |\n| --- | --- |\n')
		assert len(chunk.text) <= 200
	assert chunks[0].location.start == 1
	assert chunks[1].location.start > chunks[0].location.end


def test_small_tables_in_one_section_merge():
	text = (
		'## 枚举值\n\n**a 甲**：\n\n| 值 | 说明 |\n| --- | --- |\n| 0 | 否 |\n\n'
		'**b 乙**：\n\n| 值 | 说明 |\n| --- | --- |\n| 1 | 是 |\n'
	)
	chunks = _chunks(text, max_chars=1200, min_chars=80)
	assert len(chunks) == 1
	assert '**a 甲**' in chunks[0].text and '**b 乙**' in chunks[0].text


def test_no_fragment_shorter_than_min_chars_when_neighbour_fits():
	text = '# 标题\n\n## 一\n\n短。\n\n## 二\n\n' + '正文。' * 40 + '\n\n## 三\n\n尾。\n'
	chunks = _chunks(text, max_chars=1200, min_chars=80)
	assert all(len(chunk.text) >= 80 for chunk in chunks)


def test_trailing_thematic_break_is_not_a_chunk():
	text = '# A\n\n' + '内容。' * 40 + '\n\n---\n\n# B\n\n' + '内容。' * 40 + '\n'
	chunks = _chunks(text, min_chars=80)
	assert [chunk.title_path for chunk in chunks] == [['A'], ['B']]
	assert not chunks[0].text.endswith('---')


def test_short_heading_joins_first_piece_of_long_paragraph():
	text = '## 标题\n\n' + '很长的句子。' * 60 + '\n'
	chunks = _chunks(text, max_chars=120, min_chars=80)
	assert chunks[0].text.startswith('## 标题\n\n很长的句子。')
	assert all(len(chunk.text) <= 120 for chunk in chunks)


def test_heading_keeps_trailing_hash():
	chunks = _chunks('## C#\n\n内容\n\n## 结尾 ##\n\n内容\n')
	assert [chunk.title_path for chunk in chunks] == [['C#'], ['结尾']]


def test_short_tables_on_different_sheets_do_not_merge():
	from axc_wensuo.parsers.base import Block, ParsedDocument, Table

	blocks = []
	for name in ('收入', '成本'):
		table = Table(rows=[['科目', '金额'], ['人力', '1']], header_rows=1, sheet=name, row_start=2)
		location = Location(kind='sheet', sheet=name, row_start=2, row_end=2)
		blocks.append(Block(kind='table', start=0, end=0, text='', table=table, location=location))
	doc = ParsedDocument(doc_id='a.xlsx', doc_title='表', text='', blocks=blocks)
	chunks = chunk_document(doc, scope='x', doc_title='表', max_chars=1200, min_chars=80)
	assert [chunk.location.sheet for chunk in chunks] == ['收入', '成本']
