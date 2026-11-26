from __future__ import annotations

import json

import pytest
from helpers import run_cli

from axc_wensuo.errors import ConfigError

DOCS = {
	'苹果.md': '# 苹果\n\n苹果是一种水果。\n',
	'香蕉.md': '# 香蕉\n\n香蕉是另一种水果。\n',
}


def _write_queries(tmp_path, rows) -> str:
	path = tmp_path / 'queries.jsonl'
	path.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in rows) + '\n', encoding='utf-8')
	return str(path)

def test_eval_metrics(kb_factory, tmp_path):
	kb = kb_factory(DOCS)
	path = _write_queries(
		tmp_path,
		[
			{'id': 'q1', 'query': '苹果', 'relevant': [{'doc_id': '苹果.md'}]},
			{'id': 'q2', 'query': '香蕉', 'relevant': [{'doc_id': '香蕉.md', 'title_path': ['香蕉']}]},
			{'id': 'q3', 'query': '苹果 香蕉', 'relevant': [{'doc_id': '不存在.md'}]},
			{'id': 'q4', 'query': '火车', 'no_answer': True},
		],
	)
	result = kb.evaluate(path, ['lexical', 'fused'], k=5)
	for mode in ('lexical', 'fused'):
		metrics = result.modes[mode]
		assert metrics.hit_at_k == pytest.approx(2 / 3)
		assert metrics.top1 == pytest.approx(2 / 3)
		assert metrics.mrr_at_k == pytest.approx(2 / 3)
		assert metrics.answerable_return_rate == pytest.approx(1.0)
		assert metrics.false_return_rate == pytest.approx(0.0)
		assert metrics.n == 4
	assert result.k == 5

def test_eval_title_path_mismatch(kb_factory, tmp_path):
	kb = kb_factory(DOCS)
	path = _write_queries(
		tmp_path,
		[{'id': 'q1', 'query': '苹果', 'relevant': [{'doc_id': '苹果.md', 'title_path': ['不存在']}]}],
	)
	result = kb.evaluate(path, ['lexical'], k=5)
	assert result.modes['lexical'].hit_at_k == 0.0

def test_eval_per_query_k(kb_factory, tmp_path):
	kb = kb_factory(
		{
			'高频.md': '# 高频\n\n' + '关键词关键词关键词。' * 20 + '\n',
			'低频.md': '# 低频\n\n关键词一次。\n',
		}
	)
	path = _write_queries(
		tmp_path,
		[
			{'id': 'q1', 'query': '关键词', 'relevant': [{'doc_id': '低频.md'}], 'k': 1},
			{'id': 'q2', 'query': '关键词', 'relevant': [{'doc_id': '低频.md'}]},
		],
	)
	result = kb.evaluate(path, ['lexical'], k=5)
	# q1 只取前 1 条，高频文档排在前面 → 未命中；q2 取前 5 条 → 命中
	assert result.modes['lexical'].hit_at_k == pytest.approx(0.5)

def test_eval_null_metrics(kb_factory, tmp_path):
	kb = kb_factory(DOCS)
	only_no_answer = _write_queries(tmp_path, [{'id': 'q1', 'query': '火车', 'no_answer': True}])
	metrics = kb.evaluate(only_no_answer, ['lexical'], k=5).modes['lexical']
	assert metrics.hit_at_k is None
	assert metrics.top1 is None
	assert metrics.mrr_at_k is None
	assert metrics.answerable_return_rate is None
	assert metrics.false_return_rate == 0.0

	only_answer = _write_queries(tmp_path, [{'id': 'q1', 'query': '苹果', 'relevant': [{'doc_id': '苹果.md'}]}])
	metrics = kb.evaluate(only_answer, ['lexical'], k=5).modes['lexical']
	assert metrics.false_return_rate is None

def test_eval_does_not_record_gaps(kb_factory, tmp_path):
	kb = kb_factory(DOCS)
	path = _write_queries(tmp_path, [{'id': 'q1', 'query': '不存在标识 zz_flag', 'no_answer': True}])
	kb.evaluate(path, ['lexical'], k=5)
	assert kb.gaps().gaps == []

def test_eval_invalid_file(kb_factory, tmp_path):
	kb = kb_factory(DOCS)
	bad = tmp_path / 'bad.jsonl'
	bad.write_text('{not json}\n', encoding='utf-8')
	with pytest.raises(ConfigError):
		kb.evaluate(str(bad), ['lexical'], k=5)
	bad.write_text(json.dumps({'query': 'x'}) + '\n', encoding='utf-8')
	with pytest.raises(ConfigError):
		kb.evaluate(str(bad), ['lexical'], k=5)

def test_eval_cli(kb_factory, tmp_path):
	kb = kb_factory(DOCS)
	path = _write_queries(tmp_path, [{'id': 'q1', 'query': '苹果', 'relevant': [{'doc_id': '苹果.md'}]}])
	result = run_cli(kb.root, 'eval', path, '--modes', 'lexical,fused', '--k', '5', '--format', 'json')
	assert result.returncode == 0
	payload = json.loads(result.stdout)
	assert payload['schema_version'] == 1
	assert payload['k'] == 5
	assert payload['modes']['lexical']['hit_at_k'] == 1.0
	assert set(payload['modes']['lexical']) == {
		'hit_at_k',
		'top1',
		'mrr_at_k',
		'answerable_return_rate',
		'false_return_rate',
		'n',
	}

	result = run_cli(kb.root, 'eval', path, '--modes', 'rerank')
	assert result.returncode == 2
