from __future__ import annotations

from pathlib import Path

import pytest

from axc_wensuo.config import default_threads, global_config_path, load_config, load_global_config, resolve_root
from axc_wensuo.errors import ConfigError


def _root(tmp_path: Path) -> Path:
	root = tmp_path / 'kb'
	root.mkdir()
	return root

def test_defaults(tmp_path):
	root = _root(tmp_path)
	config = load_config(root)
	assert config.name == 'kb'
	assert config.index_dir == (root / '.wensuo').resolve()
	assert config.models_dir == (Path.home() / '.cache' / 'wensuo' / 'models').resolve()
	assert config.threads == default_threads()
	assert config.chunk.max_chars == 1200
	assert config.chunk.min_chars == 80
	assert config.lexical.tokenizer == 'cjk_bigram'
	assert config.lexical.title_weight == 2.0
	assert config.dense.enabled is True
	assert config.dense.model == 'bge-small-zh-v1.5'
	assert config.fusion.w_lexical == 1.0
	assert config.fusion.w_dense == 0.25
	assert config.lexical.k1 == 1.2
	assert config.lexical.b == 0.5
	assert config.fusion.candidates == 50
	assert config.fusion.max_per_doc == 3
	assert config.rerank.model_dir is None
	assert config.rerank.top_k == 30

def test_unknown_top_level_field(tmp_path):
	root = _root(tmp_path)
	(root / 'wensuo.yaml').write_text('nope: 1\n', encoding='utf-8')
	with pytest.raises(ConfigError, match='未知字段'):
		load_config(root)

def test_unknown_nested_field(tmp_path):
	root = _root(tmp_path)
	(root / 'wensuo.yaml').write_text('chunk:\n  max_chars: 1000\n  bogus: 1\n', encoding='utf-8')
	with pytest.raises(ConfigError, match='bogus'):
		load_config(root)

@pytest.mark.parametrize(
	'body',
	[
		'chunk: {max_chars: 80, min_chars: 80}\n',
		'chunk: {max_chars: 100, min_chars: 0}\n',
		'lexical: {b: 1.5}\n',
		'lexical: {k1: 0}\n',
		'lexical: {title_weight: -1}\n',
		'lexical: {tokenizer: nope}\n',
		'dense: {model: other}\n',
		'dense: {enabled: "yes"}\n',
		'fusion: {w_lexical: 0, w_dense: 0}\n',
		'fusion: {candidates: 0}\n',
		'fusion: {max_per_doc: -1}\n',
		'rerank: {min_score: 2}\n',
		'rerank: {top_k: 0}\n',
		'max_file_mb: 0\n',
		'threads: 0\n',
		'include: nope\n',
		'scopes: [a]\n',
		'scope_rules: [{glob: x}]\n',
		'scope_rules: [{glob: x, scope: y, extra: z}]\n',
		'index_dir: ""\n',
	],
)
def test_invalid_values(tmp_path, body):
	root = _root(tmp_path)
	(root / 'wensuo.yaml').write_text(body, encoding='utf-8')
	with pytest.raises(ConfigError):
		load_config(root)

def test_index_dir_relative_to_root(tmp_path):
	root = _root(tmp_path)
	(root / 'wensuo.yaml').write_text('index_dir: idx\nmodels_dir: models\n', encoding='utf-8')
	config = load_config(root)
	assert config.index_dir == (root / 'idx').resolve()
	assert config.models_dir == (root / 'models').resolve()

def test_scope_rules_and_descriptions(tmp_path):
	root = _root(tmp_path)
	(root / 'wensuo.yaml').write_text(
		'scopes: {数据字典: 表结构}\nscope_rules: [{glob: "**/*报销*", scope: 财务}]\n', encoding='utf-8'
	)
	config = load_config(root)
	assert config.scopes == {'数据字典': '表结构'}
	assert config.scope_rules[0].glob == '**/*报销*'
	assert config.scope_rules[0].scope == '财务'

def test_invalid_yaml(tmp_path):
	root = _root(tmp_path)
	(root / 'wensuo.yaml').write_text('foo: [\n', encoding='utf-8')
	with pytest.raises(ConfigError, match='YAML'):
		load_config(root)

def test_global_config_and_named_kb(tmp_path, monkeypatch):
	notes = tmp_path / 'notes'
	notes.mkdir()
	config_path = global_config_path()
	config_path.parent.mkdir(parents=True, exist_ok=True)
	config_path.write_text(f'default: notes\nkbs:\n  notes: {notes}\n', encoding='utf-8')
	global_config = load_global_config()
	assert global_config.default == 'notes'
	assert global_config.kbs['notes'] == notes
	assert resolve_root(None, 'notes') == notes.resolve()
	assert resolve_root(None, None) == notes.resolve()

def test_missing_named_kb(tmp_path):
	with pytest.raises(ConfigError, match='没有名为'):
		resolve_root(None, 'missing')

def test_root_and_kb_conflict(tmp_path):
	root = _root(tmp_path)
	with pytest.raises(ConfigError, match='不能同时指定'):
		resolve_root(str(root), 'notes')

def test_default_root_is_cwd(tmp_path, monkeypatch):
	root = _root(tmp_path)
	monkeypatch.chdir(root)
	assert resolve_root(None, None) == root.resolve()

def test_config_files_recorded(tmp_path, monkeypatch):
	root = _root(tmp_path)
	(root / 'wensuo.yaml').write_text('max_file_mb: 10\n', encoding='utf-8')
	config = load_config(root)
	assert config.kb_config_file == (root / 'wensuo.yaml')
	assert (root / 'wensuo.yaml') in config.config_files

def test_bool_is_not_int(tmp_path):
	root = _root(tmp_path)
	(root / 'wensuo.yaml').write_text('threads: true\n', encoding='utf-8')
	with pytest.raises(ConfigError):
		load_config(root)


@pytest.mark.parametrize(
	'body',
	[
		'lexical: {title_weight: .nan}\n',
		'lexical: {k1: .inf}\n',
		'lexical: {b: .nan}\n',
		'fusion: {w_dense: .inf}\n',
		'fusion: {w_lexical: .nan}\n',
		'rerank: {min_score: .nan}\n',
	],
)
def test_non_finite_numbers_rejected(tmp_path, body):
	root = _root(tmp_path)
	(root / 'wensuo.yaml').write_text(body, encoding='utf-8')
	with pytest.raises(ConfigError, match='有限数字'):
		load_config(root)
