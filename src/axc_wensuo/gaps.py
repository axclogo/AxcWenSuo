"""缺口清单（FR-624、FR-625）：记下“查不到”的查询，驱动补文档。"""

from __future__ import annotations

import datetime as dt
import json

from axc_wensuo.filters import Filters
from axc_wensuo.models import GapEntry, GapsResult
from axc_wensuo.store import Store


def record_gap(
	store: Store,
	*,
	query: str,
	filters: Filters,
	entry: str,
	result_count: int,
	missing_identifiers: list[str],
) -> None:
	now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
	store.record_gap(
		query=query,
		filters_json=filters.canonical_json(),
		entry=entry,
		last_result_count=result_count,
		missing_identifiers=missing_identifiers,
		now=now,
	)


def should_record(*, result_count: int, missing_identifiers: list[str]) -> bool:
	"""FR-624：结果为空，或 missing_identifiers 非空。"""
	return result_count == 0 or bool(missing_identifiers)


def list_gaps(store: Store, limit: int = 50) -> GapsResult:
	entries: list[GapEntry] = []
	for row in store.list_gaps(limit):
		entries.append(
			GapEntry(
				query=row['query'],
				filters=json.loads(row['filters']),
				entry=row['entry'],
				count=int(row['count']),
				first_seen=row['first_seen'],
				last_seen=row['last_seen'],
				last_result_count=int(row['last_result_count']),
				missing_identifiers=json.loads(row['missing_identifiers']),
			)
		)
	return GapsResult(gaps=entries)


def clear_gaps(store: Store) -> int:
	return store.clear_gaps()


__all__ = ['clear_gaps', 'list_gaps', 'record_gap', 'should_record']
