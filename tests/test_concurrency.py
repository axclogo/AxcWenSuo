from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest
from helpers import search, write, yaml_config

from axc_wensuo import KnowledgeBase
from axc_wensuo.store import IndexLock

DOC = '# 文档\n\n## 小节\n\n关键词内容。\n'


def _command(root, *args):
	return [sys.executable, '-m', 'axc_wensuo.cli', '--root', str(root), *args]

@pytest.fixture
def root(tmp_path):
	root = tmp_path / 'kb'
	root.mkdir()
	write(root, 'wensuo.yaml', yaml_config())
	for index in range(20):
		write(root, f'文档/{index:02d}.md', f'# 文档 {index}\n\n## 小节\n\n关键词内容 {index}。\n')
	return root

def test_two_syncs_are_serialized_and_index_is_valid(root):
	env = os.environ.copy()
	first = subprocess.Popen(_command(root, 'sync', '--format', 'json'), stdout=subprocess.PIPE, env=env)
	second = subprocess.Popen(_command(root, 'sync', '--format', 'json'), stdout=subprocess.PIPE, env=env)
	first_output, _ = first.communicate(timeout=120)
	second_output, _ = second.communicate(timeout=120)
	assert first.returncode == 0
	assert second.returncode == 0
	first_payload = json.loads(first_output)
	second_payload = json.loads(second_output)
	assert len(first_payload['added']) + len(second_payload['added']) == 20

	kb = KnowledgeBase(root)
	try:
		assert kb.store.count_docs() == 20
		integrity = kb.store.conn.execute('PRAGMA integrity_check').fetchone()[0]
		assert integrity == 'ok'
		assert search(kb, '关键词', sync=False).results
	finally:
		kb.close()

def test_query_returns_stale_while_another_process_syncs(root):
	env = os.environ.copy()
	kb = KnowledgeBase(root)
	try:
		kb.sync()
		lock = IndexLock(kb.config.index_dir)
		assert lock.acquire(0)
		try:
			pending = subprocess.Popen(_command(root, 'sync'), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
			search = subprocess.run(
				_command(root, 'search', '关键词', '--format', 'json'),
				capture_output=True,
				text=True,
				timeout=60,
				env=env,
			)
			assert search.returncode == 0
			payload = json.loads(search.stdout)
			assert payload['stale'] is True
			assert payload['results']
		finally:
			lock.release()
		pending.communicate(timeout=120)
		assert pending.returncode == 0
	finally:
		kb.close()
	after = subprocess.run(
		_command(root, 'search', '关键词', '--format', 'json'),
		capture_output=True,
		text=True,
		timeout=60,
		env=env,
	)
	assert json.loads(after.stdout)['stale'] is False

def test_lock_file_is_released(kb_factory):
	kb = kb_factory({'甲.md': DOC})
	kb.sync()
	lock = IndexLock(kb.config.index_dir)
	assert lock.acquire(0)
	lock.release()
	assert lock.acquire(0)
	lock.release()
	assert (
		sqlite3.connect(str(kb.config.index_dir / 'index.sqlite')).execute('PRAGMA integrity_check').fetchone()[0]
		== 'ok'
	)
