from __future__ import annotations

import pytest

from axc_wensuo.errors import ConfigError, WenSuoRuntimeError
from axc_wensuo.tokenizers import (
	Tokenizer,
	extract_ident_runs,
	extract_identifiers,
	get_tokenizer,
	identifier_hit,
	identifier_in_runs,
	normalize,
	tokenize_cjk_bigram,
)


def test_cjk_single_and_bigram():
	tokens = tokenize_cjk_bigram('用户表')
	assert tokens == ['用', '户', '表', '用户', '户表']

def test_cjk_single_character_run():
	assert tokenize_cjk_bigram('表') == ['表']

def test_ascii_identifier_tokens():
	tokens = tokenize_cjk_bigram('查 user_id 字段')
	assert 'user_id' in tokens
	assert 'user' in tokens
	assert 'id' in tokens
	assert '字段' in tokens

def test_ascii_edges_stripped():
	tokens = tokenize_cjk_bigram('见 .net. 与 v2.1-')
	assert 'net' in tokens
	assert 'v2.1' in tokens
	assert 'v2' in tokens
	assert '1' in tokens

def test_numbers_and_punctuation():
	assert tokenize_cjk_bigram('42') == ['42']
	assert tokenize_cjk_bigram('!!! ???') == []
	assert tokenize_cjk_bigram('') == []

def test_nfkc_and_lowercase():
	assert normalize('ＡＢＣ　用户') == 'abc 用户'
	tokens = tokenize_cjk_bigram('MAX_PT')
	assert 'max_pt' in tokens
	assert 'max' in tokens
	assert 'pt' in tokens

def test_extract_identifiers():
	assert extract_identifiers('查 user_id.') == ['user_id']
	assert extract_identifiers('v2.1、max_pt、id、42') == ['v2.1', 'max_pt']
	assert extract_identifiers('a_b') == ['a_b']
	assert extract_identifiers('x.y') == ['x.y']
	assert extract_identifiers('user_id user_id USERS') == ['user_id', 'users']
	assert extract_identifiers('中文查询') == []

def test_identifier_hit_boundaries():
	assert identifier_hit('表 user_id 字段', 'user_id')
	assert not identifier_hit('user_ids', 'user_id')
	assert not identifier_hit('user_id', 'user')
	assert identifier_hit('user', 'user')
	assert identifier_hit('v2.1.3', 'v2.1')
	assert not identifier_hit('xv2.1', 'v2.1')
	assert identifier_hit('前缀user_id', 'user_id')
	assert not identifier_hit('user_idx', 'user_id')

def test_identifier_in_runs():
	runs = ['user_id', 'v2.1.3']
	assert identifier_in_runs(runs, 'user_id')
	assert identifier_in_runs(runs, 'v2.1')
	assert not identifier_in_runs(runs, 'user')

def test_extract_ident_runs():
	assert extract_ident_runs('见 user_id. 与 ./config') == ['user_id', 'config']
	assert extract_ident_runs('无') == []

def test_tokenizer_object():
	tokenizer = get_tokenizer('cjk_bigram')
	assert tokenizer.name == 'cjk_bigram'
	assert tokenizer.signature.startswith('cjk_bigram:')
	assert tokenizer.counts('用户用户')['用户'] == 2

def test_unknown_tokenizer():
	with pytest.raises(ConfigError):
		Tokenizer(name='nope')

def test_jieba_missing(monkeypatch):
	import axc_wensuo.tokenizers as tokenizers_module

	monkeypatch.setattr(tokenizers_module.importlib.util, 'find_spec', lambda name: None if name == 'jieba' else True)
	with pytest.raises(WenSuoRuntimeError, match='jieba'):
		get_tokenizer('jieba')
