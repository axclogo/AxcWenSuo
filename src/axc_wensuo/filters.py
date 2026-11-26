"""检索过滤条件（FR-631）与对应的 SQL 片段。"""

from __future__ import annotations

import datetime as dt
import json
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Any


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
	seen: set[str] = set()
	result: list[str] = []
	for value in values:
		if value not in seen:
			seen.add(value)
			result.append(value)
	return tuple(result)


def normalize_file_types(values: tuple[str, ...]) -> tuple[str, ...]:
	"""扩展名统一为小写无点（FR-631）。"""
	result: list[str] = []
	for value in values:
		item = value.strip().lower()
		if item.startswith('.'):
			item = item[1:]
		if not item:
			continue
		result.append(item)
	return _dedupe(tuple(result))


@dataclass(frozen=True)
class Filters:
	""" "与"关系的过滤条件集合。"""

	scope: tuple[str, ...] = ()
	path_prefix: str | None = None
	tags: tuple[str, ...] = ()
	file_types: tuple[str, ...] = ()
	modified_after: dt.date | None = None

	def normalized(self) -> Filters:
		return replace(
			self,
			scope=_dedupe(tuple(_nfc(scope) for scope in self.scope if scope)),
			tags=_dedupe(tuple(_nfc(tag) for tag in self.tags if tag)),
			file_types=normalize_file_types(self.file_types),
			path_prefix=_nfc(self.path_prefix) if self.path_prefix else None,
		)

	def is_empty(self) -> bool:
		return not (self.scope or self.path_prefix or self.tags or self.file_types or self.modified_after)

	def to_dict(self) -> dict[str, Any]:
		"""规范化字典：键排序、去掉空值，用于缺口清单去重。"""
		data: dict[str, Any] = {}
		if self.file_types:
			data['file_types'] = list(self.file_types)
		if self.modified_after is not None:
			data['modified_after'] = self.modified_after.isoformat()
		if self.path_prefix:
			data['path_prefix'] = self.path_prefix
		if self.scope:
			data['scope'] = list(self.scope)
		if self.tags:
			data['tags'] = list(self.tags)
		return data

	def canonical_json(self) -> str:
		return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(',', ':'))

	def where(self, alias: str) -> tuple[str, list[Any]]:
		"""生成 SQL 条件（不含 ``WHERE``）。``alias`` 指向含 doc_id/scope/modified_ts 的表。"""
		conditions: list[str] = []
		params: list[Any] = []
		if self.scope:
			placeholders = ', '.join('?' for _ in self.scope)
			conditions.append(f'{alias}.scope IN ({placeholders})')
			params.extend(self.scope)
		if self.path_prefix:
			conditions.append(f"{alias}.doc_id LIKE ? ESCAPE '\\'")
			params.append(_escape_like(self.path_prefix) + '%')
		for tag in self.tags:
			conditions.append(f'EXISTS (SELECT 1 FROM doc_tags AS t WHERE t.doc_id = {alias}.doc_id AND t.tag = ?)')
			params.append(tag)
		for file_type in self.file_types:
			conditions.append(f"lower({alias}.doc_id) LIKE ? ESCAPE '\\'")
			params.append(f'%.{_escape_like(file_type)}')
		if self.modified_after is not None:
			conditions.append(f'{alias}.modified_ts >= ?')
			params.append(local_midnight(self.modified_after))
		return ' AND '.join(conditions), params


def _nfc(value: str) -> str:
	return unicodedata.normalize('NFC', value)


def _escape_like(value: str) -> str:
	return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def local_midnight(day: dt.date) -> float:
	"""本地时区该日 00:00 的时间戳（FR-631）。"""
	return dt.datetime.combine(day, dt.time.min).timestamp()


def parse_modified_after(value: str) -> dt.date:
	try:
		return dt.date.fromisoformat(value)
	except ValueError as exc:
		from axc_wensuo.errors import ConfigError

		raise ConfigError(f'--modified-after 必须是 YYYY-MM-DD 格式：{value!r}') from exc


@dataclass(frozen=True)
class QueryOptions:
	"""一次检索的全部参数。"""

	top_k: int = 5
	max_chars: int = 800
	filters: Filters = field(default_factory=Filters)
	min_score: float | None = None
