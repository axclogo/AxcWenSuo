"""Gap list: queries that returned nothing. 缺口清单。"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from axc_wensuo.filters import Filters
from axc_wensuo.models import GapEntry, GapsResult
from axc_wensuo.store import GapRecord, Store


@dataclass(frozen=True)
class GapQuery:
	"""One query that produced a gap. 一次产生缺口的查询。"""

	query: str
	filters: Filters
	entry: str
	result_count: int
	missing_identifiers: list[str]

def record_gap(store: Store, gap: GapQuery) -> None:
	"""Write one gap. 记录一次缺口。"""
	now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
	store.record_gap(
		GapRecord(
			query=gap.query,
			filters_json=gap.filters.canonical_json(),
			entry=gap.entry,
			last_result_count=gap.result_count,
			missing_identifiers=gap.missing_identifiers,
			now=now,
		)
	)

def should_record(*, result_count: int, missing_identifiers: list[str]) -> bool:
	"""Empty result or missing identifiers. 空结果或缺标识符时记录。"""
	return result_count == 0 or bool(missing_identifiers)

def list_gaps(store: Store, limit: int = 50) -> GapsResult:
	"""List gaps ordered by count and time. 按次数与时间列出缺口。"""
	entries = [
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
		for row in store.list_gaps(limit)
	]
	return GapsResult(gaps=entries)

def clear_gaps(store: Store) -> int:
	return store.clear_gaps()


__all__ = ['clear_gaps', 'list_gaps', 'record_gap', 'should_record']
