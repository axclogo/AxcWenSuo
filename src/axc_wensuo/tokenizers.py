"""分词（FR-501、FR-502）与标识符提取（第 4 节）。

默认分词器 ``cjk_bigram``：汉字输出单字 + 相邻两字；连续的 ``[a-z0-9_.\\-]`` 串输出整串，
含分隔符时再按 ``_ . -`` 拆开输出各部分。不丢弃停用词。

查询与文档使用同一分词器（FR-504）。
"""

from __future__ import annotations

import importlib.util
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from axc_wensuo.errors import ConfigError, WenSuoRuntimeError

CJK_RANGES: tuple[tuple[int, int], ...] = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))
CJK_BIGRAM_VERSION = 1
TOKENIZER_NAMES = ('cjk_bigram', 'jieba')

WORD_RE = re.compile(r'[a-z0-9_.\-]+')
SPLIT_RE = re.compile(r'[_\.\-]+')
ASCII_WORD_CHARS = frozenset('abcdefghijklmnopqrstuvwxyz0123456789_')


def normalize(text: str) -> str:
	"""NFKC 归一 + 转小写（索引与查询共用）。"""
	return unicodedata.normalize('NFKC', text).lower()


def _is_cjk(char: str) -> bool:
	code = ord(char)
	return any(low <= code <= high for low, high in CJK_RANGES)


def _word_tokens(run: str) -> list[str]:
	run = run.strip('.-')
	if not run:
		return []
	tokens = [run]
	if '_' in run or '.' in run or '-' in run:
		tokens.extend(part for part in SPLIT_RE.split(run) if part)
	return tokens


def tokenize_cjk_bigram(text: str) -> list[str]:
	tokens: list[str] = []
	normalized = normalize(text)
	index = 0
	length = len(normalized)
	while index < length:
		char = normalized[index]
		if _is_cjk(char):
			end = index + 1
			while end < length and _is_cjk(normalized[end]):
				end += 1
			run = normalized[index:end]
			tokens.extend(run)
			tokens.extend(run[pos : pos + 2] for pos in range(len(run) - 1))
			index = end
			continue
		match = WORD_RE.match(normalized, index)
		if match is not None:
			tokens.extend(_word_tokens(match.group()))
			index = match.end()
			continue
		index += 1
	return tokens


def _jieba_module():
	if importlib.util.find_spec('jieba') is None:
		raise WenSuoRuntimeError('分词器 jieba 未安装；请安装 jieba，或在 wensuo.yaml 中改用 tokenizer: cjk_bigram')
	import jieba

	return jieba


def tokenize_jieba(text: str) -> list[str]:
	jieba = _jieba_module()
	normalized = normalize(text)
	tokens: list[str] = []

	def cut_segment(segment: str) -> None:
		if not segment:
			return
		for token in jieba.lcut(segment):
			if any(_is_cjk(char) for char in token):
				tokens.append(token)

	position = 0
	for match in WORD_RE.finditer(normalized):
		cut_segment(normalized[position : match.start()])
		tokens.extend(_word_tokens(match.group()))
		position = match.end()
	cut_segment(normalized[position:])
	return tokens


def tokenizer_signature(name: str) -> str:
	if name == 'cjk_bigram':
		return f'cjk_bigram:{CJK_BIGRAM_VERSION}'
	if name == 'jieba':
		jieba = _jieba_module()
		return f'jieba:{getattr(jieba, "__version__", "unknown")}'
	raise ConfigError(f'未知分词器：{name!r}；可选：{", ".join(TOKENIZER_NAMES)}')


@dataclass(frozen=True)
class Tokenizer:
	name: str

	def __post_init__(self) -> None:
		if self.name not in TOKENIZER_NAMES:
			raise ConfigError(f'未知分词器：{self.name!r}；可选：{", ".join(TOKENIZER_NAMES)}')
		if self.name == 'jieba':
			_jieba_module()

	def tokenize(self, text: str) -> list[str]:
		if self.name == 'jieba':
			return tokenize_jieba(text)
		return tokenize_cjk_bigram(text)

	def counts(self, text: str) -> Counter[str]:
		return Counter(self.tokenize(text))

	@property
	def signature(self) -> str:
		return tokenizer_signature(self.name)


def get_tokenizer(name: str) -> Tokenizer:
	return Tokenizer(name=name)


# ---------------------------------------------------------------- 标识符


def extract_identifiers(query: str) -> list[str]:
	"""按第 4 节提取查询中的标识符，去重并保持首次出现顺序。"""
	normalized = normalize(query)
	identifiers: list[str] = []
	seen: set[str] = set()
	for match in WORD_RE.finditer(normalized):
		value = match.group().strip('.-')
		if not value:
			continue
		has_letter = any('a' <= char <= 'z' for char in value)
		if not ((len(value) >= 3 and has_letter) or '_' in value or '.' in value):
			continue
		if value not in seen:
			seen.add(value)
			identifiers.append(value)
	return identifiers


def identifier_hit(normalized_text: str, identifier: str) -> bool:
	"""标识符命中规则（第 4 节）：子串出现且前后字符都不是 ``[a-z0-9_]`` 或位于边界。"""
	start = 0
	length = len(identifier)
	while True:
		position = normalized_text.find(identifier, start)
		if position < 0:
			return False
		before = normalized_text[position - 1] if position > 0 else ''
		after_position = position + length
		after = normalized_text[after_position] if after_position < len(normalized_text) else ''
		if before not in ASCII_WORD_CHARS and after not in ASCII_WORD_CHARS:
			return True
		start = position + 1


def identifier_in_runs(runs: list[str], identifier: str) -> bool:
	"""在一组已归一的标识符串中判断是否命中。"""
	return any(identifier_hit(run, identifier) for run in runs)


def extract_ident_runs(text: str) -> list[str]:
	"""提取文本中所有 ``[a-z0-9_.\\-]`` 连续串（去首尾 ``.`` / ``-``），用于快速判断“知识库里有没有”。"""
	normalized = normalize(text)
	runs: list[str] = []
	seen: set[str] = set()
	for match in WORD_RE.finditer(normalized):
		value = match.group().strip('.-')
		if value and value not in seen:
			seen.add(value)
			runs.append(value)
	return runs
