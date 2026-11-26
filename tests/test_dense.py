from __future__ import annotations

import json

import pytest
from helpers import make_embedder_dir, write, yaml_config

DOCS = {
	'水果/苹果.md': '# 苹果\n\n苹果是一种水果，红色或绿色。\n',
	'水果/香蕉.md': '# 香蕉\n\n香蕉是黄色水果。\n',
	'交通/火车.md': '# 火车\n\n火车在铁轨上运行。\n',
}


def _kb_with_model(kb_factory, tmp_path):
	model_dir = tmp_path / 'models' / 'bge-small-zh-v1.5'
	texts = [path + text for path, text in DOCS.items()] + ['苹果', '香蕉', '火车', '水果']
	make_embedder_dir(model_dir, texts)
	config = yaml_config(dense={'enabled': True}, models_dir=str(tmp_path / 'models'))
	kb = kb_factory(DOCS, config=config)
	return kb


def test_dense_recall_ranks_semantically_close_document(kb_factory, tmp_path):
	kb = _kb_with_model(kb_factory, tmp_path)
	kb.sync()
	assert kb.store.count_missing_vectors() == 0
	engine = kb._engine('dense')
	result = engine.search('苹果', top_k=3, mode='dense')
	assert result.results
	assert result.results[0].doc_id == '水果/苹果.md'
	assert 'dense' in result.results[0].sources
	assert 'lexical' not in result.results[0].sources


def test_fused_uses_both_routes(kb_factory, tmp_path):
	kb = _kb_with_model(kb_factory, tmp_path)
	kb.sync()
	hits = kb.search('苹果', sync=False).results
	sources = [hit.sources for hit in hits]
	assert any('dense' in item for item in sources)
	assert any('lexical' in item for item in sources)
	assert hits[0].doc_id == '水果/苹果.md'


def test_dense_signature_and_reembedding_after_model_change(kb_factory, tmp_path):
	kb = _kb_with_model(kb_factory, tmp_path)
	kb.sync()
	first_signature = kb.store.dense_signature()
	assert first_signature is not None

	model_dir = tmp_path / 'models' / 'bge-small-zh-v1.5'
	make_embedder_dir(model_dir, ['苹果', '香蕉', '火车', '水果', '苹果是一种水果，红色或绿色。'])
	report = kb.sync()
	assert set(report.modified) == set(DOCS)
	assert kb.store.dense_signature() != first_signature
	assert kb.store.count_missing_vectors() == 0


def test_long_document_is_windowed(kb_factory, tmp_path):
	long_text = '苹果' * 600
	model_dir = tmp_path / 'models' / 'bge-small-zh-v1.5'
	make_embedder_dir(model_dir, ['苹果', long_text, '查询苹果'])
	config = yaml_config(dense={'enabled': True}, models_dir=str(tmp_path / 'models'))
	kb = kb_factory({'长.md': '# 长\n\n' + long_text + '\n'}, config=config)
	kb.sync()
	assert kb.store.count_missing_vectors() == 0
	hits = kb.search('苹果', sync=False).results
	assert hits


def test_dense_model_missing_is_runtime_error(kb_factory):
	config = yaml_config(dense={'enabled': True}, models_dir='missing-models')
	kb = kb_factory({'甲.md': '# 甲\n\n内容。\n'}, config=config)
	with pytest.raises(Exception) as excinfo:
		kb.sync()
	assert '向量模型未就绪' in str(excinfo.value)


def test_dense_disabled_has_no_dense_source(kb_factory):
	kb = kb_factory({'甲.md': '# 甲\n\n鲸鱼内容。\n'})
	result = kb.search('鲸鱼')
	assert 'dense' not in result.results[0].sources
	assert result.score_kind == 'rank_only'


def test_eval_dense_mode(kb_factory, tmp_path):
	kb = _kb_with_model(kb_factory, tmp_path)
	kb.sync()
	queries = tmp_path / 'queries.jsonl'
	queries.write_text(
		'\n'.join(
			[
				json.dumps({'id': 'q1', 'query': '苹果', 'relevant': [{'doc_id': '水果/苹果.md'}], 'k': 3}),
				json.dumps({'id': 'q2', 'query': '不存在的内容', 'no_answer': True}),
			]
		),
		encoding='utf-8',
	)
	result = kb.evaluate(queries, ['dense', 'fused'], k=3)
	assert result.modes['dense'].hit_at_k == 1.0
	assert result.modes['fused'].hit_at_k == 1.0
	assert result.modes['dense'].false_return_rate is not None


def test_unchanged_chunk_vectors_are_reused(kb_factory, tmp_path, monkeypatch):
	kb = _kb_with_model(kb_factory, tmp_path)
	kb.sync()
	old_chunks = kb.store.chunks_for_doc('水果/苹果.md')
	old_texts = {chunk['index_text'] for chunk in old_chunks}

	from axc_wensuo.dense import Embedder

	original = Embedder.encode_documents
	recorded: list[list[str]] = []

	def spy(self, texts):
		recorded.append(list(texts))
		return original(self, texts)

	monkeypatch.setattr(Embedder, 'encode_documents', spy)
	write(kb.root, '水果/苹果.md', '# 苹果\n\n苹果是一种水果，红色或绿色。\n\n## 补充\n\n新增的一小段内容。\n')
	kb.sync()
	embedded = [text for batch in recorded for text in batch]
	assert embedded
	assert not set(embedded) & old_texts
	assert any('补充' in text for text in embedded)


def test_doctor_reports_model_readiness(kb_factory, tmp_path):
	from helpers import make_reranker_dir

	model_dir = tmp_path / 'models' / 'bge-small-zh-v1.5'
	make_embedder_dir(model_dir, ['苹果', '内容'])
	rerank_dir = tmp_path / 'rerank'
	make_reranker_dir(rerank_dir, ['苹果', '内容'], {'苹': 1.0})
	config = yaml_config(
		dense={'enabled': True},
		models_dir=str(tmp_path / 'models'),
		rerank={'model_dir': str(rerank_dir)},
	)
	kb = kb_factory({'甲.md': '# 甲\n\n苹果内容。\n'}, config=config)
	doctor = kb.doctor()
	assert doctor.dense['ready'] is True
	assert doctor.dense['enabled'] is True
	assert doctor.rerank['ready'] is True
	assert doctor.rerank['enabled'] is True
	assert doctor.dense['identity']
	assert doctor.rerank['identity']


def test_vector_cache_reused_across_documents(kb_factory, tmp_path, monkeypatch):
	kb = _kb_with_model(kb_factory, tmp_path)
	kb.sync()
	from axc_wensuo.dense import Embedder

	original = Embedder.encode_documents
	recorded: list[list[str]] = []

	def spy(self, texts):
		recorded.append(list(texts))
		return original(self, texts)

	write(kb.root, '副本/苹果副本.md', '# 苹果\n\n苹果是一种水果，红色或绿色。\n')
	monkeypatch.setattr(Embedder, 'encode_documents', spy)
	kb.sync()
	assert recorded == []
	hits = kb.search('苹果', sync=False).results
	assert any(hit.doc_id == '副本/苹果副本.md' for hit in hits)
