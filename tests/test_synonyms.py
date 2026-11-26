from __future__ import annotations

import pytest
from helpers import search, write

from axc_wensuo.errors import ConfigError


def test_synonyms_expand_lexical_query(kb_factory):
	kb = kb_factory({'服务器.md': '# 服务器\n\n服务器运行正常。\n'})
	assert search(kb, '主机').results == []
	write(kb.root, 'synonyms.yaml', '- [主机, 服务器]\n')
	assert search(kb, '主机').results
	# Synonym edits need no reindex. 同义词变化无需重建索引。
	assert kb.store.count_docs() == 1

def test_synonyms_do_not_affect_identifiers(kb_factory):
	kb = kb_factory({'服务器.md': '# 服务器\n\n服务器运行正常。\n'})
	write(kb.root, 'synonyms.yaml', '- [主机, 服务器]\n')
	result = search(kb, 'zz_flag 主机')
	assert result.identifiers == ['zz_flag']
	assert result.missing_identifiers == ['zz_flag']
	assert result.results

def test_synonym_group_multiple_terms(kb_factory):
	kb = kb_factory({'账号.md': '# 账号\n\n登录流程说明。\n'})
	write(kb.root, 'synonyms.yaml', '- [登录, 登陆, sign in]\n')
	assert search(kb, '登陆').results
	assert search(kb, 'sign in').results

def test_invalid_synonyms_file(kb_factory):
	kb = kb_factory({'甲.md': '# 甲\n\n内容。\n'})
	write(kb.root, 'synonyms.yaml', 'not-a-list\n')
	with pytest.raises(ConfigError):
		search(kb, '内容')
	write(kb.root, 'synonyms.yaml', '- [只有一个词]\n')
	with pytest.raises(ConfigError):
		search(kb, '内容')
