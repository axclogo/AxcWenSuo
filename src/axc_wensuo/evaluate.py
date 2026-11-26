"""``ws eval`` 的评测集读取与指标计算（第 11.3 节）。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from axc_wensuo.errors import ConfigError
from axc_wensuo.models import EvalModeMetrics, EvalResult, SearchHit, SearchResult
from axc_wensuo.search import MODES

SearchFunction = Callable[[str, int, str], SearchResult]


@dataclass
class EvalQuery:
	id: str
	query: str
	relevant: list[tuple[str, list[str] | None]] = field(default_factory=list)
	no_answer: bool = False
	k: int | None = None

	@property
	def answerable(self) -> bool:
		return bool(self.relevant) and not self.no_answer


def load_queries(path: str | Path, *, default_k: int) -> list[EvalQuery]:
	file_path = Path(path)
	try:
		lines = file_path.read_text(encoding='utf-8').splitlines()
	except OSError as exc:
		raise ConfigError(f'无法读取评测集 {file_path}：{exc}') from exc
	queries: list[EvalQuery] = []
	for line_number, line in enumerate(lines, start=1):
		line = line.strip()
		if not line:
			continue
		where = f'{file_path}:{line_number}'
		try:
			raw = json.loads(line)
		except ValueError as exc:
			raise ConfigError(f'{where} 不是合法 JSON：{exc}') from exc
		if not isinstance(raw, dict):
			raise ConfigError(f'{where} 必须是 JSON 对象')
		query = raw.get('query')
		if not isinstance(query, str) or not query:
			raise ConfigError(f'{where} 缺少非空 query')
		relevant: list[tuple[str, list[str] | None]] = []
		raw_relevant = raw.get('relevant') or []
		if not isinstance(raw_relevant, list):
			raise ConfigError(f'{where} 的 relevant 必须是列表')
		for item in raw_relevant:
			if not isinstance(item, dict) or not isinstance(item.get('doc_id'), str):
				raise ConfigError(f'{where} 的 relevant 每项必须含 doc_id')
			title_path = item.get('title_path')
			if title_path is not None and (
				not isinstance(title_path, list) or not all(isinstance(part, str) for part in title_path)
			):
				raise ConfigError(f'{where} 的 title_path 必须是字符串列表')
			relevant.append((item['doc_id'], title_path))
		no_answer = bool(raw.get('no_answer', False))
		if not relevant and not no_answer:
			raise ConfigError(f'{where} 必须提供 relevant 或 no_answer')
		k_value = raw.get('k')
		if k_value is not None and (type(k_value) is not int or k_value <= 0):
			raise ConfigError(f'{where} 的 k 必须是正整数')
		queries.append(
			EvalQuery(
				id=str(raw.get('id', line_number)),
				query=query,
				relevant=relevant,
				no_answer=no_answer,
				k=k_value,
			)
		)
	if not queries:
		raise ConfigError(f'评测集为空：{file_path}')
	return queries


def evaluate(search: SearchFunction, queries: list[EvalQuery], modes: list[str], *, k: int) -> EvalResult:
	if not modes:
		raise ConfigError('至少指定一种评测模式')
	for mode in modes:
		if mode not in MODES:
			raise ConfigError(f'未知评测模式：{mode}；可选：{", ".join(MODES)}')
	metrics: dict[str, EvalModeMetrics] = {}
	for mode in modes:
		metrics[mode] = _evaluate_mode(search, queries, mode, k)
	return EvalResult(k=k, modes=metrics)


def _evaluate_mode(search: SearchFunction, queries: list[EvalQuery], mode: str, default_k: int) -> EvalModeMetrics:
	answerable_hits = 0
	answerable_returned = 0
	top1_hits = 0
	mrr_total = 0.0
	answerable_total = 0
	no_answer_total = 0
	false_returns = 0
	for item in queries:
		top_k = item.k or default_k
		results = search(item.query, top_k, mode).results
		if item.answerable:
			answerable_total += 1
			if results:
				answerable_returned += 1
			matched_rank = _first_relevant_rank(results, item.relevant)
			if matched_rank is not None:
				answerable_hits += 1
				mrr_total += 1.0 / matched_rank
				if matched_rank == 1:
					top1_hits += 1
		elif item.no_answer:
			no_answer_total += 1
			if results:
				false_returns += 1
	return EvalModeMetrics(
		hit_at_k=_ratio(answerable_hits, answerable_total),
		top1=_ratio(top1_hits, answerable_total),
		mrr_at_k=_ratio_float(mrr_total, answerable_total),
		answerable_return_rate=_ratio(answerable_returned, answerable_total),
		false_return_rate=_ratio(false_returns, no_answer_total),
		n=len(queries),
	)


def _first_relevant_rank(results: list[SearchHit], relevant: list[tuple[str, list[str] | None]]) -> int | None:
	for hit in results:
		for doc_id, title_path in relevant:
			if hit.doc_id != doc_id:
				continue
			if title_path is None or list(hit.title_path[: len(title_path)]) == list(title_path):
				return hit.rank
	return None


def _ratio(numerator: int, denominator: int) -> float | None:
	if denominator == 0:
		return None
	return numerator / denominator


def _ratio_float(numerator: float, denominator: int) -> float | None:
	if denominator == 0:
		return None
	return numerator / denominator
