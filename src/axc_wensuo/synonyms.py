"""同义词（FR-632）：``<root>/synonyms.yaml``，格式为词组列表。

对 NFKC 归一、转小写后的原始查询做子串匹配；命中组内任一词时，把组内其他词分词后并入关键词查询
的词集。向量召回、``identifiers``、``missing_identifiers`` 只用原始查询。同义词文件变化不需要重建索引。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from axc_wensuo.errors import ConfigError
from axc_wensuo.tokenizers import Tokenizer, normalize


class Synonyms:
	def __init__(self, groups: list[list[str]]) -> None:
		self.groups = groups

	def __bool__(self) -> bool:
		return bool(self.groups)

	def expanded_tokens(self, query: str, tokenizer: Tokenizer) -> list[str]:
		normalized_query = normalize(query)
		tokens: list[str] = []
		for group in self.groups:
			normalized_terms = [(term, normalize(term)) for term in group]
			matched = {term for term, normalized in normalized_terms if normalized and normalized in normalized_query}
			if not matched:
				continue
			for term, _normalized in normalized_terms:
				if term in matched:
					continue
				tokens.extend(tokenizer.tokenize(term))
		return tokens


def load_synonyms(path: str | Path) -> Synonyms:
	file_path = Path(path)
	if not file_path.is_file():
		return Synonyms([])
	try:
		data = yaml.safe_load(file_path.read_text(encoding='utf-8'))
	except (OSError, yaml.YAMLError) as exc:
		raise ConfigError(f'同义词文件 {file_path} 读取失败：{exc}') from exc
	if data is None:
		return Synonyms([])
	if not isinstance(data, list):
		raise ConfigError(f'同义词文件 {file_path} 必须是词组列表，例如 - [登录, 登陆]')
	groups: list[list[str]] = []
	for index, group in enumerate(data):
		if not isinstance(group, list) or len(group) < 2:
			raise ConfigError(f'同义词文件 {file_path} 第 {index + 1} 项必须至少包含两个词')
		terms = []
		for term in group:
			if not isinstance(term, str) or not term.strip():
				raise ConfigError(f'同义词文件 {file_path} 第 {index + 1} 项含非字符串或空词')
			terms.append(term.strip())
		groups.append(terms)
	return Synonyms(groups)


__all__ = ['Synonyms', 'load_synonyms']
