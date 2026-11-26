"""One bad file must not break the knowledge base (section 3, FR-203, FR-204, FR-206). 单个坏文件不影响整库。"""

from __future__ import annotations

import os
import sys

import pytest
from helpers import list_docs, read, run_cli, search, write, yaml_config

from axc_wensuo import KnowledgeBase
from axc_wensuo.parsers import docs_extra_available

GOOD = '# 正常文档\n\n这里提到唯一词：阿尔法。\n'


def _assert_kb_still_works(kb: KnowledgeBase) -> None:
	assert search(kb, '阿尔法').results[0].doc_id == '正常.md'
	assert read(kb, '正常.md') is not None
	assert '正常.md' in [doc.doc_id for doc in list_docs(kb).docs]


def test_broken_symlink_is_skipped(kb_factory):
	kb = kb_factory({'正常.md': GOOD})
	(kb.root / '断链.md').symlink_to(kb.root / '不存在.md')
	report = kb.sync()
	assert '断链.md' in report.skipped
	assert '正常.md' in report.added
	_assert_kb_still_works(kb)
	skipped = {entry.doc_id: entry.reason for entry in kb.doctor().skipped}
	assert '无法读取文件信息' in skipped['断链.md']


@pytest.mark.skipif(sys.platform == 'win32' or os.geteuid() == 0, reason='需要 POSIX 权限且非 root')
def test_unreadable_file_is_recorded_as_failure(kb_factory):
	kb = kb_factory({'正常.md': GOOD, '锁定.md': '# 锁定\n\n内容。\n'})
	locked = kb.root / '锁定.md'
	locked.chmod(0)
	try:
		report = kb.sync()
		assert report.failed == ['锁定.md']
		_assert_kb_still_works(kb)
		failed = {entry.doc_id: entry.reason for entry in kb.doctor().failed}
		assert '无法读取文件' in failed['锁定.md']
		assert run_cli(kb.root, 'search', '阿尔法').returncode == 0
		locked.chmod(0o644)
		again = kb.sync()
		assert again.failed == []
		assert '锁定.md' in again.added
		assert kb.doctor().failed == []
	finally:
		locked.chmod(0o644)


def test_csv_with_oversized_cell_is_recorded_as_failure(kb_factory):
	kb = kb_factory({'正常.md': GOOD, '大单元格.csv': 'a,b\n1,"' + 'x' * 140000 + '"\n'})
	report = kb.sync()
	assert report.failed == ['大单元格.csv']
	_assert_kb_still_works(kb)
	assert [entry.doc_id for entry in kb.doctor().failed] == ['大单元格.csv']


def test_failure_record_removed_when_file_deleted(kb_factory):
	kb = kb_factory({'正常.md': GOOD, '坏文件.txt': b'\xff\xff'})
	kb.sync()
	assert [entry.doc_id for entry in kb.doctor().failed] == ['坏文件.txt']
	(kb.root / '坏文件.txt').unlink()
	report = kb.sync()
	assert report.deleted == []
	assert kb.doctor().failed == []


def test_failure_record_removed_when_file_excluded(kb_factory):
	kb = kb_factory({'正常.md': GOOD, '坏文件.txt': b'\xff\xff'})
	kb.sync()
	(kb.root / 'wensuo.yaml').write_text(yaml_config(exclude=['*.txt']), encoding='utf-8')
	with KnowledgeBase(kb.root) as reopened:
		reopened.sync()
		assert reopened.doctor().failed == []
		assert [entry.doc_id for entry in reopened.doctor().skipped] == ['坏文件.txt']


def test_unchanged_failed_file_is_not_rehashed(kb_factory, monkeypatch):
	kb = kb_factory({'正常.md': GOOD, '坏文件.txt': b'\xff\xff'})
	kb.sync()
	import axc_wensuo.kb as kb_module

	hashed: list[str] = []
	original = kb_module.file_sha256

	def counting(path):
		hashed.append(str(path))
		return original(path)

	monkeypatch.setattr(kb_module, 'file_sha256', counting)
	report = kb.sync()
	assert report.failed == ['坏文件.txt']
	assert hashed == []
	# Touch without content change: hash once, still cached. 只改时间：算一次哈希，仍然命中缓存。
	os.utime(kb.root / '坏文件.txt', ns=(1_000_000_000, 1_000_000_000))
	report = kb.sync()
	assert report.failed == ['坏文件.txt']
	assert len(hashed) == 1
	hashed.clear()
	kb.sync()
	assert hashed == []


def test_scope_rules_change_updates_indexed_scopes(kb_factory):
	kb = kb_factory({'笔记/甲.md': GOOD.replace('正常文档', '甲')})
	kb.sync()
	assert list_docs(kb).docs[0].scope == '笔记'
	(kb.root / 'wensuo.yaml').write_text(
		yaml_config(scope_rules=[{'glob': '**/*.md', 'scope': '新专题'}]), encoding='utf-8'
	)
	with KnowledgeBase(kb.root) as reopened:
		report = reopened.sync()
		assert report.modified == ['笔记/甲.md']
		assert list_docs(reopened).docs[0].scope == '新专题'
		assert search(reopened, '阿尔法', scope=('新专题',)).results
		assert search(reopened, '阿尔法', scope=('笔记',)).results == []


@pytest.mark.skipif(not docs_extra_available(), reason='缺少 docs 扩展（docling）')
def test_docling_cache_replaced_after_modification(kb_factory):
	kb = kb_factory({'页面.html': '<html><body><h1>旧标题</h1><p>旧内容。</p></body></html>'})
	kb.sync()
	parsed_dir = kb.config.index_dir / 'parsed'
	assert len(list(parsed_dir.iterdir())) == 1
	write(kb.root, '页面.html', '<html><body><h1>新标题</h1><p>新内容，长度不同。</p></body></html>')
	kb.sync()
	assert len(list(parsed_dir.iterdir())) == 1
	assert search(kb, '新内容').results
