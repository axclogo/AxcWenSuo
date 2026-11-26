from __future__ import annotations

import datetime as dt
import os

import pytest
from helpers import list_docs, read, search, yaml_config

from axc_wensuo.errors import ConfigError

WHALE = '# 海洋笔记\n\n蓝色鲸鱼在深海缓慢游动。\n'
USER_DOC = (
	'# 用户表\n\n## 字段\n\n用户表包含 `user_id` 字段，`user_id` 是主键。\n\n## 索引\n\n在 `user_id` 上建立唯一索引。\n'
)


def _search_hits(kb, query, **kwargs):
	return search(kb, query, **kwargs).results

def test_two_character_term_is_recalled(kb_factory):
	kb = kb_factory({'海洋.md': WHALE})
	assert _search_hits(kb, '鲸鱼')

def test_identifier_and_split_identifier(kb_factory):
	kb = kb_factory({'用户.md': USER_DOC})
	assert _search_hits(kb, 'user_id')
	result = search(kb, 'user')
	assert result.results
	assert result.identifiers == ['user']
	assert _search_hits(kb, 'user')  # user_id 被拆成 user / id，user 也能命中

def test_identifier_not_matching_longer_identifier(kb_factory):
	kb = kb_factory({'用户.md': USER_DOC, '其他.md': '# 其他\n\n字段 user_idx 用于测试。\n'})
	hits = _search_hits(kb, 'user_id')
	assert hits[0].doc_id == '用户.md'

def test_long_table_row_contains_header(kb_factory):
	rows = '\n'.join(f'| {index:03d} | 设备{index} | 说明{index} |' for index in range(1, 121))
	kb = kb_factory({'设备.md': '# 台账\n\n| 编号 | 名称 | 说明 |\n| --- | --- | --- |\n' + rows + '\n'})
	hits = _search_hits(kb, '设备90', top_k=10, max_chars=5000)
	hit = next(hit for hit in hits if '设备90' in hit.text)
	assert hit.text.startswith('| 编号 | 名称 | 说明 |')
	assert '设备90' in hit.text

def test_filters_scope_path_tags_file_type_modified(kb_factory):
	config = yaml_config(
		scopes={'财务': '财务'},
		scope_rules=[{'glob': '**/报销*', 'scope': '财务'}],
	)
	kb = kb_factory(
		{
			'财务/报销单.md': '---\ntags: [财务, 流程]\n---\n\n# 报销单\n\n差旅报销需要发票。\n',
			'财务/报销附件.csv': '项目,金额\n发票,100\n',
			'运维/部署.md': '---\ntags: [运维]\n---\n\n# 部署\n\n差旅报销无关内容。\n',
		},
		config=config,
	)
	kb.sync()
	old = dt.datetime(2020, 1, 2, 12, 0).timestamp()
	os.utime(kb.root / '运维/部署.md', (old, old))
	kb.sync()

	assert {hit.doc_id for hit in _search_hits(kb, '报销', scope=['财务'])} == {
		'财务/报销单.md',
		'财务/报销附件.csv',
	}
	assert {hit.doc_id for hit in _search_hits(kb, '报销', path_prefix='财务/报销单')} == {'财务/报销单.md'}
	assert {hit.doc_id for hit in _search_hits(kb, '报销', tags=['财务'])} == {'财务/报销单.md'}
	assert {hit.doc_id for hit in _search_hits(kb, '报销', tags=['财务', '流程'])} == {'财务/报销单.md'}
	assert _search_hits(kb, '报销', tags=['财务', '运维']) == []
	assert {hit.doc_id for hit in _search_hits(kb, '报销', file_types=['md'])} == {'财务/报销单.md', '运维/部署.md'}
	assert {hit.doc_id for hit in _search_hits(kb, '报销', file_types=['.CSV'])} == {'财务/报销附件.csv'}
	after = dt.date(2021, 1, 1)
	assert {hit.doc_id for hit in _search_hits(kb, '报销', modified_after=after)} == {
		'财务/报销单.md',
		'财务/报销附件.csv',
	}
	assert {hit.doc_id for hit in _search_hits(kb, '报销', modified_after=dt.date(2019, 1, 1))} == {
		'财务/报销单.md',
		'财务/报销附件.csv',
		'运维/部署.md',
	}

def test_path_prefix_is_raw_string_prefix(kb_factory):
	kb = kb_factory(
		{
			'财务/a/x.md': '# A\n\n关键词甲。\n',
			'财务/abc/y.md': '# B\n\n关键词甲。\n',
		}
	)
	assert {hit.doc_id for hit in _search_hits(kb, '关键词甲', path_prefix='财务/a')} == {
		'财务/a/x.md',
		'财务/abc/y.md',
	}

def test_line_location_covers_hit(kb_factory):
	text = '# 标题\n\n第一段无关。\n\n蓝色鲸鱼在深海游动。\n'
	kb = kb_factory({'海洋.md': text})
	resolved = _search_hits(kb, '鲸鱼')[0]
	assert resolved.location.kind == 'lines'
	lines = text.splitlines()
	segment = '\n'.join(lines[resolved.location.start - 1 : resolved.location.end])
	assert '鲸鱼' in segment

def test_title_path_matches_headings(kb_factory):
	kb = kb_factory({'部署.md': '# 部署\n\n## 环境变量\n\n配置 `MAX_CONN` 后重启。\n'})
	hit = _search_hits(kb, 'MAX_CONN')[0]
	assert hit.title_path == ['部署', '环境变量']

def test_missing_identifiers_and_gaps(kb_factory):
	kb = kb_factory({'甲.md': '# 甲\n\n这里没有那个东西。\n'})
	result = search(kb, 'zz_flag 字段')
	assert result.identifiers == ['zz_flag']
	assert result.missing_identifiers == ['zz_flag']
	assert result.results == []
	gaps = kb.gaps().gaps
	assert len(gaps) == 1
	assert gaps[0].query == 'zz_flag 字段'
	assert gaps[0].missing_identifiers == ['zz_flag']
	assert gaps[0].count == 1

	result = search(kb, 'zz_flag 字段')
	assert result.missing_identifiers == ['zz_flag']
	assert kb.gaps().gaps[0].count == 2

	search(kb, 'zz_flag 字段', entry='cli')
	assert len(kb.gaps().gaps) == 2
	assert {gap.entry for gap in kb.gaps().gaps} == {'api', 'cli'}

	kb.clear_gaps()
	assert kb.gaps().gaps == []

def test_gap_filters_canonical(kb_factory):
	kb = kb_factory({'甲.md': '# 甲\n\n内容。\n'})
	search(kb, 'nothing_here', scope=['甲'], tags=['x'])
	search(kb, 'nothing_here', scope=['甲'], tags=['x'])
	gaps = kb.gaps().gaps
	assert len(gaps) == 1
	assert gaps[0].filters == {'scope': ['甲'], 'tags': ['x']}
	assert gaps[0].count == 2

def test_min_score_requires_rerank(kb_factory):
	kb = kb_factory({'甲.md': '# 甲\n\n内容。\n'})
	with pytest.raises(ConfigError, match='min_score'):
		search(kb, '内容', min_score=0.5)

def test_deterministic_results(kb_factory):
	kb = kb_factory(
		{
			'甲.md': '# 甲\n\n用户表与 user_id 字段说明。\n',
			'乙.md': '# 乙\n\n用户表包含 user_id 与 nickname。\n',
			'丙.md': '# 丙\n\n用户表索引。\n',
		}
	)
	first = search(kb, '用户表 user_id').to_dict()
	for _ in range(100):
		assert search(kb, '用户表 user_id', sync=False).to_dict() == first

def test_max_per_doc(kb_factory):
	sections = '\n\n'.join(f'## 小节{i}\n\n关键词贯穿整篇文档。' for i in range(8))
	kb = kb_factory({'多段.md': '# 多段\n\n' + sections + '\n', '其他.md': '# 其他\n\n关键词贯穿整篇文档。\n'})
	hits = _search_hits(kb, '关键词', top_k=10)
	per_doc: dict[str, int] = {}
	for hit in hits:
		per_doc[hit.doc_id] = per_doc.get(hit.doc_id, 0) + 1
	assert per_doc['多段.md'] <= 3

def test_identifier_layering_beats_lexical_score(kb_factory):
	kb = kb_factory(
		{
			'命中.md': '# 命中\n\n字段 user_id 说明。\n',
			'无关.md': '# 无关\n\n' + 'user id 与 user id 反复出现。' * 30 + '\n',
		}
	)
	hits = _search_hits(kb, 'user_id', top_k=5)
	assert hits[0].doc_id == '命中.md'

def test_truncation(kb_factory):
	long_paragraph = '长内容' * 300
	kb = kb_factory({'长.md': '# 长\n\n' + long_paragraph + '\n'})
	result = search(kb, '长内容', max_chars=100)
	hit = result.results[0]
	assert len(hit.text) == 100
	assert hit.truncated is True
	assert result.score_kind == 'rank_only'
	assert 'dense' not in hit.sources

def test_query_validation(kb_factory):
	kb = kb_factory({'甲.md': '# 甲\n\n内容。\n'})
	with pytest.raises(ConfigError):
		search(kb, 'a' * 1001)
	with pytest.raises(ConfigError, match='没有任何词'):
		search(kb, '!!!')
	with pytest.raises(ConfigError):
		search(kb, '内容', top_k=0)
	with pytest.raises(ConfigError):
		search(kb, '内容', max_chars=0)

def test_show_and_read_api(kb_factory):
	text = '# 部署\n\n## 变量\n\n配置 `MAX_CONN`。\n\n## 步骤\n\n第一步。\n'
	kb = kb_factory({'部署.md': text})
	hit = _search_hits(kb, 'MAX_CONN')[0]
	detail = kb.show(hit.chunk_id)
	assert detail is not None
	assert detail.text == hit.text
	assert kb.show('not-a-chunk') is None

	result = read(kb, '部署.md')
	assert result is not None
	assert result.text == text
	assert result.next_offset is None
	result = read(kb, '部署.md', section='部署 > 变量')
	assert result is not None
	assert result.text.startswith('## 变量')
	assert '第一步' not in result.text
	slice_one = read(kb, '部署.md', offset=0, max_chars=10)
	assert slice_one is not None
	assert len(slice_one.text) == 10
	assert slice_one.next_offset == 10
	assert read(kb, '不存在.md') is None
	assert read(kb, '部署.md', section='部署 > 不存在') is None


def test_path_prefix_is_case_sensitive(kb_factory):
	# Distinct names so case-insensitive file systems keep both. 名字不同，大小写不敏感的文件系统也能共存。
	kb = kb_factory({'Docs/a.md': '# A\n\n关键词乙。\n', 'docsx/b.md': '# B\n\n关键词乙。\n'})
	assert {hit.doc_id for hit in _search_hits(kb, '关键词乙', path_prefix='docs')} == {'docsx/b.md'}
	assert {hit.doc_id for hit in _search_hits(kb, '关键词乙', path_prefix='Docs')} == {'Docs/a.md'}
	assert {doc.doc_id for doc in list_docs(kb, path_prefix='docs').docs} == {'docsx/b.md'}


def test_path_prefix_with_like_wildcards_is_literal(kb_factory):
	docs = {'a_b/x.md': '# X\n\n关键词丙。\n', 'acb/y.md': '# Y\n\n关键词丙。\n', '100%/z.md': '# Z\n\n关键词丙。\n'}
	kb = kb_factory(docs)
	assert {hit.doc_id for hit in _search_hits(kb, '关键词丙', path_prefix='a_b')} == {'a_b/x.md'}
	assert {hit.doc_id for hit in _search_hits(kb, '关键词丙', path_prefix='100%')} == {'100%/z.md'}


def test_missing_identifiers_use_substring_with_boundaries(kb_factory):
	kb = kb_factory({'甲.md': '# 甲\n\n关联条件 `a.user_id = b.id`，另有 abc 与 user_ids。\n'})
	assert search(kb, 'user_id 关联').missing_identifiers == []
	assert search(kb, 'b.id 关联').missing_identifiers == []
	# Boundary rule from section 4: user is not hit by user_id or user_ids. 第 4 节边界规则。
	assert search(kb, 'user 关联').missing_identifiers == ['user']
	# Underscore is literal, not a LIKE wildcard. 下划线按字面比较，不是通配符。
	assert search(kb, 'a_c 关联').missing_identifiers == ['a_c']


def test_expand_section_returns_whole_section_once(kb_factory):
	part_one = '第一部分说明。' * 30
	part_two = '第二部分说明，包含关键词丁。' * 20
	text = f'# 手册\n\n## 安装\n\n{part_one}\n\n{part_two}\n\n## 其他\n\n' + '无关内容。' * 30 + '\n'
	kb = kb_factory({'手册.md': text}, config=yaml_config(chunk={'max_chars': 300, 'min_chars': 80}))
	plain = search(kb, '关键词丁', top_k=5).results
	assert plain[0].expanded is False
	assert len({hit.chunk_id for hit in plain}) == len(plain)
	expanded = search(kb, '关键词丁', top_k=5, expand_section=True).results
	install = [hit for hit in expanded if hit.title_path == ['手册', '安装']]
	assert len(install) == 1
	assert install[0].expanded is True
	assert install[0].text.startswith('# 手册\n\n## 安装') and part_two[:20] in install[0].text
	assert install[0].location.start == 1
	lines = text.splitlines()
	assert '关键词丁' in '\n'.join(lines[install[0].location.start - 1 : install[0].location.end])


def test_lexical_top_rank_bonus():
	from axc_wensuo.search import LEXICAL_TOP_BONUS, RRF_K, _top_bonus

	assert [_top_bonus(rank) for rank in (1, 2, 3, 4)] == [*LEXICAL_TOP_BONUS, LEXICAL_TOP_BONUS[1], 0.0]
	# Lexical #1 alone outranks lexical #4 plus dense #1. 只有关键词第 1 名的段排在关键词第 4 名加向量第 1 名之前。
	lexical_first = 1 / (RRF_K + 1) + _top_bonus(1)
	lexical_fourth_with_dense = 1 / (RRF_K + 4) + 0.25 / (RRF_K + 1)
	assert lexical_first > lexical_fourth_with_dense


def test_lexical_top_hit_survives_dense_competition(kb_factory, tmp_path):
	from helpers import make_embedder_dir

	docs = {
		'甲.md': '# 甲\n\n独有词宇宙飞船在这里出现。\n',
		'乙.md': '# 乙\n\n飞船飞船飞船，其他内容其他内容。\n',
	}
	model_dir = tmp_path / 'models' / 'bge-small-zh-v1.5'
	make_embedder_dir(model_dir, [path + text for path, text in docs.items()] + ['宇宙飞船'])
	kb = kb_factory(docs, config=yaml_config(dense={'enabled': True}, models_dir=str(tmp_path / 'models')))
	lexical = search(kb, '宇宙飞船', mode='lexical').results
	fused = search(kb, '宇宙飞船').results
	assert fused[0].chunk_id == lexical[0].chunk_id


def test_question_words_do_not_drive_lexical_ranking(kb_factory):
	docs = {
		'索引.md': '# 索引\n\n权限怎么看？订单怎么看？报表怎么看？怎么排查问题？\n',
		'权限.md': '# 权限\n\n后台权限排查：先看角色。\n',
	}
	kb = kb_factory(docs)
	assert search(kb, '权限怎么排查').results[0].doc_id == '权限.md'
	# Query of question words only keeps its tokens. 只有疑问词的查询仍能检索。
	assert search(kb, '怎么').results[0].doc_id == '索引.md'
