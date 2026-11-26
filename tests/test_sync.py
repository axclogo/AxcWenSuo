from __future__ import annotations

import shutil

import pytest
from helpers import list_docs, search, write, yaml_config

from axc_wensuo import KnowledgeBase
from axc_wensuo.errors import WenSuoRuntimeError
from axc_wensuo.store import IndexLock

DOC_A = '# 甲文档\n\n这里提到唯一词：阿尔法。\n'
DOC_B = '# 乙文档\n\n这里提到唯一词：贝塔。\n'


def test_first_sync_and_noop(tmp_path):
	root = tmp_path / 'kb'
	root.mkdir()
	(root / 'wensuo.yaml').write_text(yaml_config(), encoding='utf-8')
	write(root, '甲.md', DOC_A)
	write(root, '乙.md', DOC_B)
	kb = KnowledgeBase(root)
	try:
		report = kb.sync()
		assert sorted(report.added) == ['乙.md', '甲.md']
		assert report.modified == []
		assert report.deleted == []
		assert report.chunks_total == 2
		assert report.seconds >= 0
		again = kb.sync()
		assert again.added == [] and again.modified == [] and again.deleted == []
		assert kb.store.count_docs() == 2
	finally:
		kb.close()

def test_add_modify_delete(kb_factory):
	kb = kb_factory({'甲.md': DOC_A})
	assert '阿尔法' in search(kb, '阿尔法').results[0].text

	write(kb.root, '乙.md', DOC_B)
	assert search(kb, '贝塔').results[0].doc_id == '乙.md'

	write(kb.root, '乙.md', '# 乙文档\n\n这里提到唯一词：伽马。\n')
	result = search(kb, '贝塔')
	assert result.results == []
	result = search(kb, '伽马')
	assert result.results[0].doc_id == '乙.md'

	(kb.root / '乙.md').unlink()
	result = search(kb, '伽马')
	assert result.results == []
	assert [doc.doc_id for doc in list_docs(kb).docs] == ['甲.md']

def test_index_rebuild_is_identical(kb_factory):
	kb = kb_factory(
		{
			'运维/部署.md': '# 部署\n\n## 变量\n\n配置 `DATABASE_URL` 与 `MAX_CONN`。\n',
			'数据/表.md': '# 表\n\n| 字段 | 类型 |\n| --- | --- |\n| user_id | BIGINT |\n',
		}
	)
	kb.sync()
	before = [search(kb, query, sync=False).to_dict() for query in ('部署', 'user_id', 'DATABASE_URL')]
	index_dir = kb.config.index_dir
	kb.close()
	shutil.rmtree(index_dir)
	reopened = KnowledgeBase(kb.root)
	try:
		after = [reopened.search(query).to_dict() for query in ('部署', 'user_id', 'DATABASE_URL')]
		assert after == before
	finally:
		reopened.close()

def test_failure_is_cached_and_not_retried(kb_factory, monkeypatch):
	kb = kb_factory({'坏文件.txt': b'\xff\xff'})
	import axc_wensuo.kb as kb_module

	original = kb_module.parse_file
	calls = {'value': 0}

	def counting(*args, **kwargs):
		calls['value'] += 1
		return original(*args, **kwargs)

	report = kb.sync()
	assert report.failed == ['坏文件.txt']
	assert kb.store.get_failure('坏文件.txt') is not None

	monkeypatch.setattr(kb_module, 'parse_file', counting)
	again = kb.sync()
	assert again.failed == ['坏文件.txt']
	assert calls['value'] == 0

	doctor = kb.doctor()
	assert [entry.doc_id for entry in doctor.failed] == ['坏文件.txt']

def test_empty_file_is_added_without_chunks(kb_factory):
	kb = kb_factory({'空.md': '', '正常.md': DOC_A})
	report = kb.sync()
	assert '空.md' in report.added
	documents = {doc.doc_id: doc for doc in list_docs(kb).docs}
	assert documents['空.md'].chunks == 0
	assert documents['正常.md'].chunks >= 1

def test_chunk_config_change_forces_reindex(kb_factory):
	kb = kb_factory({'长.md': '# 标题\n\n' + '段落内容。' * 80 + '\n'})
	first = kb.sync()
	assert first.added == ['长.md']
	before_chunks = kb.store.count_chunks()
	index_dir = kb.config.index_dir
	kb.close()

	write(kb.root, 'wensuo.yaml', yaml_config(chunk={'max_chars': 100, 'min_chars': 20}))
	reopened = KnowledgeBase(kb.root)
	try:
		report = reopened.sync()
		assert report.modified == ['长.md']
		assert reopened.store.count_chunks() > before_chunks
		assert reopened.store.chunk_signature() is not None
	finally:
		reopened.close()
	assert (index_dir / 'index.sqlite').exists()

def test_touched_file_does_not_reparse(kb_factory, monkeypatch):
	kb = kb_factory({'甲.md': DOC_A})
	kb.sync()
	import axc_wensuo.kb as kb_module

	original = kb_module.parse_file
	calls = {'value': 0}

	def counting(*args, **kwargs):
		calls['value'] += 1
		return original(*args, **kwargs)

	monkeypatch.setattr(kb_module, 'parse_file', counting)
	path = kb.root / '甲.md'
	path.write_text(DOC_A, encoding='utf-8')
	report = kb.sync()
	assert report.added == [] and report.modified == []
	assert calls['value'] == 0

def test_sync_lock_timeout(kb_factory):
	kb = kb_factory({'甲.md': DOC_A})
	kb.sync()
	lock = IndexLock(kb.config.index_dir)
	assert lock.acquire(0)
	try:
		with pytest.raises(WenSuoRuntimeError):
			kb.sync(wait=False)
	finally:
		lock.release()

def test_auto_sync_stale_when_locked(kb_factory):
	kb = kb_factory({'甲.md': DOC_A})
	kb.sync()
	lock = IndexLock(kb.config.index_dir)
	assert lock.acquire(0)
	try:
		result = search(kb, '阿尔法')
		assert result.stale is True
	finally:
		lock.release()
	assert search(kb, '阿尔法').stale is False

def test_auto_sync_error_when_never_indexed(kb_factory):
	kb = kb_factory({'甲.md': DOC_A})
	lock = IndexLock(kb.config.index_dir)
	assert lock.acquire(0)
	try:
		with pytest.raises(WenSuoRuntimeError, match='索引尚未建立'):
			search(kb, '阿尔法')
	finally:
		lock.release()

def test_skipped_files_and_reasons(kb_factory, corpus_copy):
	(corpus_copy / 'wensuo.yaml').write_text(yaml_config(), encoding='utf-8')
	docs_available = __import__('axc_wensuo.parsers', fromlist=['docs_extra_available']).docs_extra_available()
	if docs_available:
		# PDF needs external Docling model assets; unrelated to this test. PDF 需要外部 Docling 模型。
		(corpus_copy / '数据' / '巡检报告.pdf').unlink()
	kb = KnowledgeBase(corpus_copy)
	try:
		doctor = kb.doctor()
		reasons = {entry.doc_id: entry.reason for entry in doctor.skipped}
		if not __import__('axc_wensuo.parsers', fromlist=['docs_extra_available']).docs_extra_available():
			assert reasons['数据/巡检报告.pdf'] == '缺少 docs 扩展'
			assert reasons['数据/年度总结.docx'] == '缺少 docs 扩展'
		report = kb.sync()
		if not docs_available:
			assert '数据/巡检报告.pdf' in report.skipped
		assert '运维/部署手册.md' in report.added
	finally:
		kb.close()

def test_too_large_and_excluded_files(tmp_path):
	root = tmp_path / 'kb'
	root.mkdir()
	(root / 'wensuo.yaml').write_text(
		yaml_config(max_file_mb=1, exclude=['数据/**'], include=['**/*.md']), encoding='utf-8'
	)
	write(root, '大.md', 'x' * (1024 * 1024 + 10))
	write(root, '数据/排除.md', DOC_A)
	write(root, '保留/保留.md', DOC_B)
	kb = KnowledgeBase(root)
	try:
		report = kb.sync()
		assert report.skipped == ['大.md', '数据/排除.md']
		assert [doc.doc_id for doc in list_docs(kb).docs] == ['保留/保留.md']
		reasons = {entry.doc_id: entry.reason for entry in kb.doctor().skipped}
		assert 'max_file_mb' in reasons['大.md']
		assert 'exclude' in reasons['数据/排除.md']
	finally:
		kb.close()

@pytest.mark.skipif(__import__('sys').platform == 'win32', reason='Windows 创建符号链接需要额外权限')
def test_symlinked_file_followed_dir_link_not_followed(tmp_path):
	root = tmp_path / 'kb'
	root.mkdir()
	(root / 'wensuo.yaml').write_text(yaml_config(), encoding='utf-8')
	outside = tmp_path / 'outside'
	outside.mkdir()
	write(outside, '外部.md', DOC_B)
	(root / '链接文件.md').symlink_to(outside / '外部.md')
	(root / '链接目录').symlink_to(outside, target_is_directory=True)
	kb = KnowledgeBase(root)
	try:
		kb.sync()
		doc_ids = [doc.doc_id for doc in list_docs(kb).docs]
		assert '链接文件.md' in doc_ids
		assert not any(doc_id.startswith('链接目录/') for doc_id in doc_ids)
	finally:
		kb.close()

def test_modify_one_file_reparses_only_that_file(kb_factory, monkeypatch):
	kb = kb_factory({'甲.md': DOC_A, '乙.md': DOC_B})
	kb.sync()
	import axc_wensuo.kb as kb_module

	original = kb_module.parse_file
	calls: list[str] = []

	def counting(path, doc_id, **kwargs):
		calls.append(doc_id)
		return original(path, doc_id, **kwargs)

	monkeypatch.setattr(kb_module, 'parse_file', counting)
	write(kb.root, '乙.md', DOC_B + '\n补充一段。\n')
	report = kb.sync()
	assert report.modified == ['乙.md']
	assert calls == ['乙.md']

def test_write_failure_propagates_and_rolls_back(kb_factory, monkeypatch):
	kb = kb_factory({'甲.md': '# 甲\n\n独特词汇甲乙丙。\n'})
	import axc_wensuo.store as store_module

	original = store_module.Store.insert_postings
	state = {'failed': False}

	def flaky(self, rows):
		if not state['failed']:
			state['failed'] = True
			raise RuntimeError('模拟写入失败')
		return original(self, rows)

	monkeypatch.setattr(store_module.Store, 'insert_postings', flaky)
	with pytest.raises(RuntimeError):
		kb.sync()
	assert kb.store.count_chunks() == 0
	assert kb.store.get_doc('甲.md') is None

	hits = kb.search('独特词汇')
	assert hits.results
