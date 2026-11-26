"""全局配置与知识库配置（``wensuo.yaml``）的加载、校验、合并。

命令行参数优先于 ``wensuo.yaml``，``wensuo.yaml`` 优先于默认值（FR-1101）。
所有配置错误都抛 :class:`~axc_wensuo.errors.ConfigError`，错误信息带字段路径。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from axc_wensuo.errors import ConfigError
from axc_wensuo.models import ScopeRule

DEFAULT_MODEL = 'bge-small-zh-v1.5'
GLOBAL_CONFIG_NAME = 'config.yaml'
KB_CONFIG_NAME = 'wensuo.yaml'
SYNONYMS_NAME = 'synonyms.yaml'


@dataclass(frozen=True)
class ChunkConfig:
	max_chars: int = 1200
	min_chars: int = 80


@dataclass(frozen=True)
class LexicalConfig:
	tokenizer: str = 'cjk_bigram'
	title_weight: float = 2.0
	k1: float = 1.2
	b: float = 0.75


@dataclass(frozen=True)
class DenseConfig:
	enabled: bool = True
	model: str = DEFAULT_MODEL


@dataclass(frozen=True)
class FusionConfig:
	w_lexical: float = 1.0
	w_dense: float = 0.5
	candidates: int = 50
	max_per_doc: int = 3


@dataclass(frozen=True)
class RerankConfig:
	model_dir: Path | None = None
	top_k: int = 30
	min_score: float | None = None

	@property
	def enabled(self) -> bool:
		return self.model_dir is not None


@dataclass(frozen=True)
class GlobalConfig:
	default: str | None = None
	kbs: dict[str, Path] = field(default_factory=dict)
	source: Path | None = None
	exists: bool = False

	def root_for(self, name: str) -> Path:
		try:
			return self.kbs[name]
		except KeyError as exc:
			configured = ', '.join(sorted(self.kbs)) or '无'
			raise ConfigError(f'全局配置中没有名为 {name!r} 的知识库；已配置：{configured}') from exc


@dataclass(frozen=True)
class Config:
	root: Path
	name: str
	index_dir: Path
	models_dir: Path
	threads: int
	include: tuple[str, ...] = ()
	exclude: tuple[str, ...] = ()
	max_file_mb: int = 50
	scopes: dict[str, str] = field(default_factory=dict)
	scope_rules: tuple[ScopeRule, ...] = ()
	chunk: ChunkConfig = field(default_factory=ChunkConfig)
	lexical: LexicalConfig = field(default_factory=LexicalConfig)
	dense: DenseConfig = field(default_factory=DenseConfig)
	fusion: FusionConfig = field(default_factory=FusionConfig)
	rerank: RerankConfig = field(default_factory=RerankConfig)
	config_files: tuple[Path, ...] = ()
	kb_config_file: Path | None = None

	def with_threads(self, threads: int | None) -> Config:
		"""返回应用命令行 ``--threads`` 后的配置。"""
		if threads is None:
			return self
		_require_positive_int(threads, 'threads')
		return replace(self, threads=threads)


def default_threads() -> int:
	cpu = os.cpu_count() or 1
	return max(1, cpu // 2)


def global_config_path() -> Path:
	"""全局配置文件路径（FR-102）。"""
	if sys.platform == 'win32':
		appdata = os.environ.get('APPDATA')
		base = Path(appdata) if appdata else Path.home() / 'AppData' / 'Roaming'
		return base / 'wensuo' / GLOBAL_CONFIG_NAME
	return Path.home() / '.config' / 'wensuo' / GLOBAL_CONFIG_NAME


def _read_yaml(path: Path) -> Any:
	try:
		text = path.read_text(encoding='utf-8')
	except OSError as exc:
		raise ConfigError(f'无法读取配置文件 {path}：{exc}') from exc
	try:
		data = yaml.safe_load(text)
	except yaml.YAMLError as exc:
		raise ConfigError(f'配置文件 {path} 不是合法 YAML：{exc}') from exc
	return {} if data is None else data


def load_global_config() -> GlobalConfig:
	"""加载全局配置；文件不存在时返回空配置。"""
	path = global_config_path()
	if not path.exists():
		return GlobalConfig()
	data = _read_yaml(path)
	if not isinstance(data, dict):
		raise ConfigError(f'{path}: 顶层必须是映射')
	unknown = set(data) - {'default', 'kbs'}
	if unknown:
		raise ConfigError(f'{path}: 未知字段 {", ".join(sorted(unknown))}')
	default = data.get('default')
	if default is not None and not isinstance(default, str):
		raise ConfigError(f'{path}: default 必须是字符串')
	raw_kbs = data.get('kbs') or {}
	if not isinstance(raw_kbs, dict):
		raise ConfigError(f'{path}: kbs 必须是映射')
	kbs: dict[str, Path] = {}
	for name, value in raw_kbs.items():
		if not isinstance(name, str) or not isinstance(value, str) or not value:
			raise ConfigError(f'{path}: kbs 的键和值都必须是非空字符串')
		kbs[name] = Path(value).expanduser()
	return GlobalConfig(default=default, kbs=kbs, source=path, exists=True)


def resolve_root(root: str | None, kb: str | None) -> Path:
	"""按 FR-101 确定知识库根目录。"""
	if root and kb:
		raise ConfigError('--root 与 --kb 不能同时指定')
	if kb:
		global_config = load_global_config()
		path = global_config.root_for(kb)
		if not path.is_dir():
			raise ConfigError(f'知识库 {kb!r} 的根目录不存在：{path}')
		return path.resolve()
	if root:
		path = Path(root).expanduser()
		if not path.is_dir():
			raise ConfigError(f'知识库根目录不存在：{path}')
		return path.resolve()
	global_config = load_global_config()
	if global_config.default:
		path = global_config.root_for(global_config.default)
		if not path.is_dir():
			raise ConfigError(f'默认知识库的根目录不存在：{path}')
		return path.resolve()
	path = Path.cwd()
	if not path.is_dir():
		raise ConfigError(f'当前目录不存在：{path}')
	return path.resolve()


def load_config(root: str | Path, *, kb_config_file: Path | None = None) -> Config:
	"""加载并校验一个知识库的配置。"""
	root_path = Path(root).expanduser().resolve()
	if not root_path.is_dir():
		raise ConfigError(f'知识库根目录不存在：{root_path}')
	config_file = kb_config_file or (root_path / KB_CONFIG_NAME)
	raw: dict[str, Any] = {}
	config_files: list[Path] = [path for path in (global_config_path(), config_file) if path.is_file()]
	if config_file.is_file():
		data = _read_yaml(config_file)
		if not isinstance(data, dict):
			raise ConfigError(f'{config_file}: 顶层必须是映射')
		raw = data
	allowed_keys = {
		'name',
		'index_dir',
		'models_dir',
		'threads',
		'include',
		'exclude',
		'max_file_mb',
		'scopes',
		'scope_rules',
		'chunk',
		'lexical',
		'dense',
		'fusion',
		'rerank',
	}
	_validate_unknown(raw, allowed_keys, str(config_file))

	name = _get_str(raw, 'name', default=root_path.name, path=str(config_file))
	index_dir = _resolve_path(_get_optional_str(raw, 'index_dir', str(config_file)), root_path / '.wensuo', root_path)
	models_dir = _resolve_path(
		_get_optional_str(raw, 'models_dir', str(config_file)), Path.home() / '.cache' / 'wensuo' / 'models', root_path
	)
	threads = _get_optional_positive_int(raw, 'threads', str(config_file)) or default_threads()
	include = tuple(_get_str_list(raw, 'include', str(config_file)))
	exclude = tuple(_get_str_list(raw, 'exclude', str(config_file)))
	max_file_mb = _get_positive_int(raw, 'max_file_mb', 50, str(config_file))
	scopes = _get_scopes(raw, str(config_file))
	scope_rules = _get_scope_rules(raw, str(config_file))
	chunk = _get_chunk(raw.get('chunk'), str(config_file), 'chunk') if 'chunk' in raw else ChunkConfig()
	lexical = _get_lexical(raw.get('lexical'), str(config_file), 'lexical') if 'lexical' in raw else LexicalConfig()
	dense = _get_dense(raw.get('dense'), str(config_file), 'dense') if 'dense' in raw else DenseConfig()
	fusion = _get_fusion(raw.get('fusion'), str(config_file), 'fusion') if 'fusion' in raw else FusionConfig()
	rerank = _get_rerank(raw.get('rerank'), str(config_file), 'rerank') if 'rerank' in raw else RerankConfig()
	_validate_relations(chunk, lexical, fusion)
	return Config(
		root=root_path,
		name=name,
		index_dir=index_dir,
		models_dir=models_dir,
		threads=threads,
		include=include,
		exclude=exclude,
		max_file_mb=max_file_mb,
		scopes=scopes,
		scope_rules=scope_rules,
		chunk=chunk,
		lexical=lexical,
		dense=dense,
		fusion=fusion,
		rerank=rerank,
		config_files=tuple(config_files),
		kb_config_file=config_file if config_file.is_file() else None,
	)


def _validate_unknown(data: dict[str, Any], allowed: set[str], where: str) -> None:
	unknown = set(data) - allowed
	if unknown:
		raise ConfigError(f'{where}: 未知字段 {", ".join(sorted(unknown))}')


def _where(location: str, key: str) -> str:
	return f'{location}: {key}' if location else key


def _require_mapping(value: Any, where: str) -> dict[str, Any]:
	if not isinstance(value, dict):
		raise ConfigError(f'{where} 必须是映射')
	return value


def _get_str(data: dict[str, Any], key: str, *, default: str, path: str) -> str:
	value = data.get(key, default)
	if not isinstance(value, str) or not value:
		raise ConfigError(f'{_where(path, key)} 必须是非空字符串')
	return value


def _get_optional_str(data: dict[str, Any], key: str, path: str) -> str | None:
	value = data.get(key)
	if value is None:
		return None
	if not isinstance(value, str) or not value:
		raise ConfigError(f'{_where(path, key)} 必须是非空字符串')
	return value


def _get_str_list(data: dict[str, Any], key: str, path: str) -> list[str]:
	value = data.get(key, [])
	if not isinstance(value, list):
		raise ConfigError(f'{_where(path, key)} 必须是字符串列表')
	for item in value:
		if not isinstance(item, str) or not item:
			raise ConfigError(f'{_where(path, key)} 必须是字符串列表')
	return list(value)


def _get_positive_int(data: dict[str, Any], key: str, default: int, path: str) -> int:
	value = data.get(key, default)
	_require_positive_int(value, _where(path, key))
	return int(value)


def _get_optional_positive_int(data: dict[str, Any], key: str, path: str) -> int | None:
	value = data.get(key)
	if value is None:
		return None
	_require_positive_int(value, _where(path, key))
	return int(value)


def _require_positive_int(value: Any, where: str) -> None:
	if type(value) is not int or value <= 0:
		raise ConfigError(f'{where} 必须是正整数')


def _get_scopes(data: dict[str, Any], path: str) -> dict[str, str]:
	value = data.get('scopes', {})
	if not isinstance(value, dict):
		raise ConfigError(f'{_where(path, "scopes")} 必须是映射')
	scopes: dict[str, str] = {}
	for name, description in value.items():
		if not isinstance(name, str) or not name or not isinstance(description, str):
			raise ConfigError(f'{_where(path, "scopes")} 的键必须是非空字符串、值必须是字符串')
		scopes[name] = description
	return scopes


def _get_scope_rules(data: dict[str, Any], path: str) -> tuple[ScopeRule, ...]:
	value = data.get('scope_rules', [])
	if not isinstance(value, list):
		raise ConfigError(f'{_where(path, "scope_rules")} 必须是列表')
	rules: list[ScopeRule] = []
	for index, item in enumerate(value):
		where = f'{_where(path, "scope_rules")}[{index}]'
		if not isinstance(item, dict) or set(item) != {'glob', 'scope'}:
			raise ConfigError(f'{where} 必须且只能包含 glob 和 scope')
		glob = item['glob']
		scope = item['scope']
		if not isinstance(glob, str) or not glob or not isinstance(scope, str) or not scope:
			raise ConfigError(f'{where} 的 glob 和 scope 必须是非空字符串')
		rules.append(ScopeRule(glob=glob, scope=scope))
	return tuple(rules)


def _resolve_path(value: str | None, default: Path, root: Path) -> Path:
	path = Path(value).expanduser() if value else default
	if not path.is_absolute():
		path = root / path
	return path.resolve()


def _get_chunk(value: Any, path: str, key: str) -> ChunkConfig:
	where = _where(path, key)
	data = _require_mapping(value, where)
	_validate_unknown(data, {'max_chars', 'min_chars'}, where)
	max_chars = data.get('max_chars', 1200)
	min_chars = data.get('min_chars', 80)
	for name, item in (('max_chars', max_chars), ('min_chars', min_chars)):
		if type(item) is not int or item <= 0:
			raise ConfigError(f'{where}.{name} 必须是正整数')
	if max_chars <= min_chars:
		raise ConfigError(f'{where}: 必须满足 max_chars > min_chars ≥ 1')
	return ChunkConfig(max_chars=max_chars, min_chars=min_chars)


def _get_lexical(value: Any, path: str, key: str) -> LexicalConfig:
	where = _where(path, key)
	data = _require_mapping(value, where)
	_validate_unknown(data, {'tokenizer', 'title_weight', 'k1', 'b'}, where)
	tokenizer = data.get('tokenizer', 'cjk_bigram')
	if tokenizer not in ('cjk_bigram', 'jieba'):
		raise ConfigError(f'{where}.tokenizer 只接受 cjk_bigram 或 jieba')
	title_weight = _number(data.get('title_weight', 2), f'{where}.title_weight')
	if title_weight < 0:
		raise ConfigError(f'{where}.title_weight 必须 ≥ 0')
	k1 = _number(data.get('k1', 1.2), f'{where}.k1')
	if k1 <= 0:
		raise ConfigError(f'{where}.k1 必须 > 0')
	b = _number(data.get('b', 0.75), f'{where}.b')
	if not 0 <= b <= 1:
		raise ConfigError(f'{where}.b 必须在 [0, 1] 内')
	return LexicalConfig(tokenizer=str(tokenizer), title_weight=title_weight, k1=k1, b=b)


def _get_dense(value: Any, path: str, key: str) -> DenseConfig:
	where = _where(path, key)
	data = _require_mapping(value, where)
	_validate_unknown(data, {'enabled', 'model'}, where)
	enabled = data.get('enabled', True)
	if type(enabled) is not bool:
		raise ConfigError(f'{where}.enabled 必须是布尔值')
	model = data.get('model', DEFAULT_MODEL)
	if model != DEFAULT_MODEL:
		raise ConfigError(f'{where}.model 只接受 {DEFAULT_MODEL}')
	return DenseConfig(enabled=enabled, model=str(model))


def _get_fusion(value: Any, path: str, key: str) -> FusionConfig:
	where = _where(path, key)
	data = _require_mapping(value, where)
	_validate_unknown(data, {'w_lexical', 'w_dense', 'candidates', 'max_per_doc'}, where)
	w_lexical = _number(data.get('w_lexical', 1.0), f'{where}.w_lexical')
	w_dense = _number(data.get('w_dense', 0.5), f'{where}.w_dense')
	if w_lexical < 0 or w_dense < 0:
		raise ConfigError(f'{where}: w_lexical 和 w_dense 必须 ≥ 0')
	if w_lexical + w_dense <= 0:
		raise ConfigError(f'{where}: w_lexical + w_dense 必须 > 0')
	candidates = data.get('candidates', 50)
	max_per_doc = data.get('max_per_doc', 3)
	for name, item in (('candidates', candidates), ('max_per_doc', max_per_doc)):
		if type(item) is not int or item <= 0:
			raise ConfigError(f'{where}.{name} 必须是正整数')
	return FusionConfig(w_lexical=w_lexical, w_dense=w_dense, candidates=candidates, max_per_doc=max_per_doc)


def _get_rerank(value: Any, path: str, key: str) -> RerankConfig:
	where = _where(path, key)
	data = _require_mapping(value, where)
	_validate_unknown(data, {'model_dir', 'top_k', 'min_score'}, where)
	model_dir_raw = data.get('model_dir')
	if model_dir_raw is not None and (not isinstance(model_dir_raw, str) or not model_dir_raw):
		raise ConfigError(f'{where}.model_dir 必须是字符串或 null')
	model_dir = Path(model_dir_raw).expanduser() if model_dir_raw else None
	if model_dir is not None and not model_dir.is_absolute():
		model_dir = (Path(path).parent if path else Path.cwd()) / model_dir
	top_k = data.get('top_k', 30)
	if type(top_k) is not int or top_k <= 0:
		raise ConfigError(f'{where}.top_k 必须是正整数')
	min_score = data.get('min_score')
	if min_score is not None:
		value_number = _number(min_score, f'{where}.min_score')
		if not 0 <= value_number <= 1:
			raise ConfigError(f'{where}.min_score 必须在 [0, 1] 内')
		min_score = value_number
	return RerankConfig(model_dir=model_dir.resolve() if model_dir else None, top_k=top_k, min_score=min_score)


def _number(value: Any, where: str) -> float:
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		raise ConfigError(f'{where} 必须是数字')
	return float(value)


def _validate_relations(chunk: ChunkConfig, lexical: LexicalConfig, fusion: FusionConfig) -> None:
	if chunk.max_chars <= chunk.min_chars:
		raise ConfigError('chunk: 必须满足 max_chars > min_chars')
	if lexical.title_weight < 0 or lexical.k1 <= 0 or not 0 <= lexical.b <= 1:
		raise ConfigError('lexical: 参数越界')
	if fusion.candidates <= 0 or fusion.max_per_doc <= 0:
		raise ConfigError('fusion: candidates 和 max_per_doc 必须是正整数')
