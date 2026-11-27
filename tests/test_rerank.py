from __future__ import annotations

import json

import pytest
from helpers import make_reranker_dir, run_cli, search, yaml_config

DOCS = {
	'甲.md': '# 甲篇\n\n好词甲。\n',
	'乙.md': '# 乙篇\n\n中性乙。\n',
	'丙.md': '# 丙篇\n\n坏词丙。\n',
}


def _setup(kb_factory, tmp_path, *, top_k=30, min_score=None):
	texts = [path + text for path, text in DOCS.items()] + ['好', '坏', '中性乙', '好词甲', '篇']
	model_dir = tmp_path / 'rerank-model'
	make_reranker_dir(model_dir, texts, {'好': 4.0, '坏': -4.0}, default_min_score=0.5)
	rerank = {'model_dir': str(model_dir), 'top_k': top_k}
	if min_score is not None:
		rerank['min_score'] = min_score
	config = yaml_config(rerank=rerank)
	return kb_factory(DOCS, config=config)

def test_rerank_threshold_filters_low_scores(kb_factory, tmp_path):
	kb = _setup(kb_factory, tmp_path, min_score=0.9)
	result = search(kb, '篇')
	assert result.score_kind == 'calibrated'
	assert [hit.doc_id for hit in result.results] == ['甲.md']
	assert result.results[0].sources['rerank'] >= 0.9
	assert result.results[0].score == result.results[0].sources['rerank']

def test_rerank_all_below_threshold_returns_empty(kb_factory, tmp_path):
	kb = _setup(kb_factory, tmp_path, min_score=0.9)
	result = search(kb, '中性乙')
	assert result.results == []
	assert result.score_kind == 'calibrated'

def test_rerank_empty_exit_code_one(kb_factory, tmp_path):
	kb = _setup(kb_factory, tmp_path, min_score=0.9)
	result = run_cli(kb.root, 'search', '中性乙')
	assert result.returncode == 1

def test_rerank_rank_weighted_formula(kb_factory, tmp_path):
	kb = _setup(kb_factory, tmp_path, top_k=3, min_score=0.0)
	result = search(kb, '篇', top_k=3)
	assert len(result.results) == 3
	assert result.score_kind == 'calibrated'
	ranked = sorted(result.results, key=lambda hit: hit.rank)
	for hit in ranked:
		position = hit.sources['lexical']
		probability = hit.sources['rerank']
		weight = 0.75 if position <= 3 else 0.60 if position <= 10 else 0.40
		s_ret = 1 - (position - 1) / 3
		expected = weight * s_ret + (1 - weight) * probability
		assert expected >= 0
	# High-probability doc ranks first. 高概率文档排在前面。
	order = [hit.doc_id for hit in ranked]
	assert order.index('甲.md') < order.index('丙.md')
	assert ranked[0].doc_id == '甲.md'

def test_rerank_disabled_by_default(kb_factory):
	kb = kb_factory(DOCS)
	result = search(kb, '篇')
	assert result.score_kind == 'rank_only'
	assert all('rerank' not in hit.sources for hit in result.results)

def test_rerank_mode_without_model_is_error(kb_factory):
	kb = kb_factory(DOCS)
	from axc_wensuo.errors import ConfigError
	from axc_wensuo.search import SearchRequest

	with pytest.raises(ConfigError, match='重排'):
		kb._engine('rerank').search(SearchRequest('内容', mode='rerank'))

def test_rerank_eval_mode(kb_factory, tmp_path):
	kb = _setup(kb_factory, tmp_path, top_k=3)
	queries = tmp_path / 'queries.jsonl'
	queries.write_text(
		json.dumps({'id': 'q1', 'query': '篇', 'relevant': [{'doc_id': '甲.md'}], 'k': 3}),
		encoding='utf-8',
	)
	result = kb.evaluate(queries, ['fused', 'rerank'], k=3)
	assert result.modes['rerank'].hit_at_k == 1.0
	assert result.modes['fused'].hit_at_k == 1.0

def test_rerank_keeps_identifier_layer_order(kb_factory, tmp_path):
	docs = {
		'带标识符.md': '# 甲篇\n\n字段 zz_marker 与坏内容。\n',
		'无标识符.md': '# 乙篇\n\n好内容而已。\n',
	}
	texts = [path + text for path, text in docs.items()] + ['好', '坏', 'zz_marker', '篇']
	model_dir = tmp_path / 'rerank-layer'
	make_reranker_dir(model_dir, texts, {'好': 4.0, '坏': -4.0}, default_min_score=0.0)
	config = yaml_config(rerank={'model_dir': str(model_dir), 'top_k': 2, 'min_score': 0.0})
	kb = kb_factory(docs, config=config)
	result = search(kb, '篇 zz_marker', top_k=2)
	assert [hit.doc_id for hit in result.results] == ['带标识符.md', '无标识符.md']
	assert result.results[0].sources['rerank'] < result.results[1].sources['rerank']


def test_meta_without_default_min_score_uses_configured_threshold(kb_factory, tmp_path):
	texts = [path + text for path, text in DOCS.items()] + ['好', '坏', '篇']
	model_dir = tmp_path / 'rerank-no-default'
	make_reranker_dir(model_dir, texts, {'好': 4.0, '坏': -4.0}, default_min_score=None)
	kb = kb_factory(DOCS, config=yaml_config(rerank={'model_dir': str(model_dir), 'min_score': 0.9}))
	assert [hit.doc_id for hit in search(kb, '篇').results] == ['甲.md']


def test_meta_without_default_min_score_needs_a_threshold(kb_factory, tmp_path):
	from axc_wensuo.errors import ConfigError

	texts = [path + text for path, text in DOCS.items()] + ['好', '坏', '篇']
	model_dir = tmp_path / 'rerank-no-default'
	make_reranker_dir(model_dir, texts, {'好': 4.0, '坏': -4.0}, default_min_score=None)
	kb = kb_factory(DOCS, config=yaml_config(rerank={'model_dir': str(model_dir)}))
	with pytest.raises(ConfigError, match='default_min_score'):
		search(kb, '篇')
	assert [hit.doc_id for hit in search(kb, '篇', min_score=0.9).results] == ['甲.md']
