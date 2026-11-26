"""Evaluation queries and metrics. 评测集与指标。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from axc_wensuo.errors import ConfigError
from axc_wensuo.models import EvalModeMetrics, EvalResult, SearchHit, SearchResult
from axc_wensuo.search import MODES, SearchRequest

SearchFunction = Callable[[SearchRequest], SearchResult]


@dataclass
class EvalQuery:
	"""One evaluation query. 一条评测查询。"""

	id: str
	query: str
	relevant: list[tuple[str, list[str] | None]] = field(default_factory=list)
	no_answer: bool = False
	k: int | None = None

	@property
	def answerable(self) -> bool:
		return bool(self.relevant) and not self.no_answer

def load_queries(path: str | Path, *, default_k: int) -> list[EvalQuery]:
	"""Load a JSONL evaluation set. 读取 JSONL 评测集。"""
	file_path = Path(path)
	try:
		lines = file_path.read_text(encoding='utf-8').splitlines()
	except OSError as exc:
		raise ConfigError(f'无法读取评测集 {file_path}：{exc}') from exc
	queries = [
		_parse_query(line, file_path, line_number)
		for line_number, line in enumerate(lines, start=1)
		if line.strip()
	]
	if not queries:
		raise ConfigError(f'评测集为空：{file_path}')
	return queries

def _parse_query(line: str, file_path: Path, line_number: int) -> EvalQuery:
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
	relevant = _parse_relevant(raw.get('relevant') or [], where)
	no_answer = bool(raw.get('no_answer', False))
	if not relevant and not no_answer:
		raise ConfigError(f'{where} 必须提供 relevant 或 no_answer')
	k_value = raw.get('k')
	if k_value is not None and (type(k_value) is not int or k_value <= 0):
		raise ConfigError(f'{where} 的 k 必须是正整数')
	return EvalQuery(
		id=str(raw.get('id', line_number)),
		query=query,
		relevant=relevant,
		no_answer=no_answer,
		k=k_value,
	)

def _parse_relevant(items: object, where: str) -> list[tuple[str, list[str] | None]]:
	if not isinstance(items, list):
		raise ConfigError(f'{where} 的 relevant 必须是列表')
	relevant: list[tuple[str, list[str] | None]] = []
	for item in items:
		if not isinstance(item, dict) or not isinstance(item.get('doc_id'), str):
			raise ConfigError(f'{where} 的 relevant 每项必须含 doc_id')
		title_path = item.get('title_path')
		if title_path is not None and (
			not isinstance(title_path, list) or not all(isinstance(part, str) for part in title_path)
		):
			raise ConfigError(f'{where} 的 title_path 必须是字符串列表')
		relevant.append((item['doc_id'], title_path))
	return relevant

def evaluate(search: SearchFunction, queries: list[EvalQuery], modes: list[str], *, k: int) -> EvalResult:
	"""Evaluate all modes. 计算所有模式的指标。"""
	if not modes:
		raise ConfigError('至少指定一种评测模式')
	for mode in modes:
		if mode not in MODES:
			raise ConfigError(f'未知评测模式：{mode}；可选：{", ".join(MODES)}')
	return EvalResult(k=k, modes={mode: _evaluate_mode(search, queries, mode, k) for mode in modes})

def _evaluate_mode(search: SearchFunction, queries: list[EvalQuery], mode: str, default_k: int) -> EvalModeMetrics:
	answerable_total = sum(1 for item in queries if item.answerable)
	no_answer_total = sum(1 for item in queries if item.no_answer and not item.answerable)
	hits = 0
	returned = 0
	top1 = 0
	mrr = 0.0
	false_returns = 0
	for item in queries:
		top_k = item.k or default_k
		request = SearchRequest(item.query, top_k=top_k, mode=mode, max_chars=1)
		results = search(request).results
		if item.answerable:
			returned += 1 if results else 0
			rank = _first_relevant_rank(results, item.relevant)
			if rank is not None:
				hits += 1
				mrr += 1.0 / rank
				top1 += 1 if rank == 1 else 0
		elif item.no_answer and results:
			false_returns += 1
	return EvalModeMetrics(
		hit_at_k=_ratio(hits, answerable_total),
		top1=_ratio(top1, answerable_total),
		mrr_at_k=_ratio(mrr, answerable_total),
		answerable_return_rate=_ratio(returned, answerable_total),
		false_return_rate=_ratio(false_returns, no_answer_total),
		n=len(queries),
	)

def _first_relevant_rank(results: list[SearchHit], relevant: list[tuple[str, list[str] | None]]) -> int | None:
	for hit in results:
		for doc_id, title_path in relevant:
			if hit.doc_id == doc_id and (title_path is None or hit.title_path[: len(title_path)] == title_path):
				return hit.rank
	return None

def _ratio(numerator: float, denominator: int) -> float | None:
	return None if denominator == 0 else numerator / denominator
