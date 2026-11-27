"""Retrieval pipeline. 检索流水线。"""

from __future__ import annotations

from dataclasses import dataclass, field

from axc_wensuo.config import Config
from axc_wensuo.dense import DenseIndex, Embedder
from axc_wensuo.errors import ConfigError
from axc_wensuo.filters import Filters
from axc_wensuo.lexical import LexicalIndex
from axc_wensuo.models import Location, SearchHit, SearchResult
from axc_wensuo.rerank import Reranker
from axc_wensuo.store import Store
from axc_wensuo.synonyms import Synonyms
from axc_wensuo.tokenizers import (
	Tokenizer,
	extract_identifiers,
	identifier_hit,
	identifier_in_runs,
	strip_question_words,
)

MODES = ('lexical', 'dense', 'fused', 'rerank')
RRF_K = 20
# Lexical rank 1 / 2-3 bonus that protects exact matches (from qmd). 关键词第 1、2~3 名加分，保护精确命中（借鉴 qmd）。
LEXICAL_TOP_BONUS = (0.01, 0.005)
MAX_QUERY_CHARS = 1000


@dataclass(frozen=True)
class SearchRequest:
	"""One search call. 一次检索请求。"""

	query: str
	top_k: int = 5
	filters: Filters = field(default_factory=Filters)
	min_score: float | None = None
	max_chars: int = 800
	mode: str | None = None
	stale: bool = False
	expand_section: bool = False

@dataclass
class SearchResources:
	"""Optional models and synonyms. 可选的模型与同义词资源。"""

	embedder: Embedder | None = None
	reranker: Reranker | None = None
	synonyms: Synonyms = field(default_factory=lambda: Synonyms([]))

@dataclass
class _Candidate:
	chunk_pk: int
	chunk_id: str
	data: dict = field(default_factory=dict)
	sources: dict[str, float] = field(default_factory=dict)
	fused: float = 0.0
	layer: int = 0
	final: float = 0.0
	probability: float = 0.0
	rank: int = 0

class SearchEngine:
	def __init__(
		self,
		store: Store,
		config: Config,
		tokenizer: Tokenizer,
		resources: SearchResources | None = None,
	) -> None:
		self.store = store
		self.config = config
		self.tokenizer = tokenizer
		self.embedder = resources.embedder if resources else None
		self.reranker = resources.reranker if resources else None
		self.synonyms = resources.synonyms if resources else Synonyms([])
		self.lexical = LexicalIndex(store, config.lexical, tokenizer)
		self.dense = DenseIndex(store) if self.embedder is not None else None

	def search(self, request: SearchRequest) -> SearchResult:
		"""Run one request. 执行一次检索。"""
		reranking = self._reranking_active(request.mode)
		self._validate(request, reranking)
		identifiers = extract_identifiers(request.query)
		missing = [item for item in identifiers if not _present(self.store, item)]
		tokens = self._query_tokens(request.query)
		candidates = self._recall(request, tokens)
		items = self._attach(candidates)
		if not items:
			return self._result(request, [], identifiers, missing)
		self._fuse(items)
		self._layers(items, identifiers)
		self._sort_by_fused(items)
		if reranking:
			items = self._rerank(request.query, items, request.min_score)
		items = self._limit(items, request.top_k, request.expand_section)
		hits = self._hits(items, reranking, request)
		return self._result(request, hits, identifiers, missing)

	def _validate(self, request: SearchRequest, reranking: bool) -> None:
		if len(request.query) > MAX_QUERY_CHARS:
			raise ConfigError(f'查询最长 {MAX_QUERY_CHARS} 个码点，当前 {len(request.query)} 个')
		if request.top_k <= 0:
			raise ConfigError('top_k 必须是正整数')
		if request.max_chars <= 0:
			raise ConfigError('max_chars 必须是正整数')
		if request.mode is not None and request.mode not in MODES:
			raise ConfigError(f'未知检索模式：{request.mode}；可选：{", ".join(MODES)}')
		if request.mode == 'rerank' and self.reranker is None:
			raise ConfigError('未配置重排模型，不能使用 rerank 模式')
		if request.mode == 'dense' and self.embedder is None:
			raise ConfigError('向量检索未启用，不能使用 dense 模式')
		if not reranking and request.min_score is not None:
			raise ConfigError('未开启重排时不能使用 min_score 阈值（分数只能比较先后）')
		if request.min_score is not None and not 0 <= request.min_score <= 1:
			raise ConfigError('min_score 必须在 [0, 1] 内')

	def _query_tokens(self, query: str) -> list[str]:
		# A query made only of question words keeps them. 只有疑问词的查询保留疑问词。
		tokens = self.tokenizer.tokenize(strip_question_words(query)) or self.tokenizer.tokenize(query)
		if self.synonyms:
			tokens = list(dict.fromkeys(tokens + self.synonyms.expanded_tokens(query, self.tokenizer)))
		if not tokens:
			raise ConfigError('查询分词后没有任何词')
		return tokens

	def _recall(self, request: SearchRequest, tokens: list[str]) -> dict[int, _Candidate]:
		candidates: dict[int, _Candidate] = {}
		filters = request.filters.normalized()
		if request.mode in (None, 'lexical', 'fused', 'rerank'):
			for hit in self.lexical.search(tokens, filters, self.config.fusion.candidates):
				candidates[hit.chunk_pk] = _Candidate(hit.chunk_pk, hit.chunk_id, sources={'lexical': hit.score})
		if request.mode in (None, 'dense', 'fused', 'rerank') and self.embedder is not None:
			assert self.dense is not None
			query_vector = self.embedder.encode_query(request.query)
			for hit in self.dense.search(query_vector, filters, self.config.fusion.candidates):
				candidate = candidates.setdefault(hit.chunk_pk, _Candidate(hit.chunk_pk, hit.chunk_id))
				candidate.sources['dense'] = hit.score
		return candidates

	def _attach(self, candidates: dict[int, _Candidate]) -> list[_Candidate]:
		data_map = self.store.chunks_by_pks(list(candidates))
		items = [item for item in candidates.values() if item.chunk_pk in data_map]
		for item in items:
			item.data = data_map[item.chunk_pk]
		return items

	def _fuse(self, items: list[_Candidate]) -> None:
		lexical_rank = _rank_by_sources(items, 'lexical')
		dense_rank = _rank_by_sources(items, 'dense')
		for item in items:
			item.fused = 0.0
			if item.chunk_pk in lexical_rank:
				item.fused += self.config.fusion.w_lexical / (RRF_K + lexical_rank[item.chunk_pk])
				item.fused += _top_bonus(lexical_rank[item.chunk_pk]) if self.config.fusion.w_lexical > 0 else 0.0
			if item.chunk_pk in dense_rank:
				item.fused += self.config.fusion.w_dense / (RRF_K + dense_rank[item.chunk_pk])
			item.sources = {}
			if item.chunk_pk in lexical_rank:
				item.sources['lexical'] = lexical_rank[item.chunk_pk]
			if item.chunk_pk in dense_rank:
				item.sources['dense'] = dense_rank[item.chunk_pk]

	def _layers(self, items: list[_Candidate], identifiers: list[str]) -> None:
		runs = self.store.ident_runs_for_chunks([item.chunk_pk for item in items]) if identifiers else {}
		for item in items:
			item.layer = sum(1 for name in identifiers if identifier_in_runs(runs.get(item.chunk_pk, []), name))

	def _sort_by_fused(self, items: list[_Candidate]) -> None:
		items.sort(key=lambda item: (-item.layer, -item.fused, item.chunk_id))
		for rank, item in enumerate(items, start=1):
			item.rank = rank

	def _rerank(self, query: str, items: list[_Candidate], min_score: float | None) -> list[_Candidate]:
		assert self.reranker is not None
		selected = items[: self.config.rerank.top_k]
		passages = [(item.data['title_path'], item.data['text']) for item in selected]
		probabilities = self.reranker.score(query, passages)
		threshold = min_score if min_score is not None else self.config.rerank.min_score
		if threshold is None:
			threshold = self.reranker.meta.default_min_score
		if threshold is None:
			raise ConfigError(
				'重排模型 model_meta.json 没有 default_min_score；请在 wensuo.yaml 设置 rerank.min_score 或传入 min_score'
			)
		kept: list[_Candidate] = []
		for item, probability in zip(selected, probabilities, strict=True):
			item.probability = probability
			if probability < threshold:
				continue
			weight = 0.75 if item.rank <= 3 else 0.60 if item.rank <= 10 else 0.40
			item.final = weight * (1 - (item.rank - 1) / self.config.rerank.top_k) + (1 - weight) * probability
			kept.append(item)
		kept.sort(key=lambda item: (-item.layer, -item.final, item.chunk_id))
		return kept

	def _limit(self, items: list[_Candidate], top_k: int, expand_section: bool) -> list[_Candidate]:
		counts: dict[str, int] = {}
		sections: set[tuple[str, tuple[str, ...]]] = set()
		kept: list[_Candidate] = []
		for item in items:
			doc_id = item.data['doc_id']
			section = (doc_id, tuple(item.data['title_path']))
			if counts.get(doc_id, 0) >= self.config.fusion.max_per_doc or (expand_section and section in sections):
				continue
			sections.add(section)
			counts[doc_id] = counts.get(doc_id, 0) + 1
			kept.append(item)
		return kept[:top_k]

	def _hits(self, items: list[_Candidate], reranking: bool, request: SearchRequest) -> list[SearchHit]:
		hits: list[SearchHit] = []
		for rank, item in enumerate(items, start=1):
			data = item.data
			text, location = self._section(data) if request.expand_section else (data['text'], data['location'])
			truncated = len(text) > request.max_chars
			sources = dict(item.sources)
			if reranking:
				sources['rerank'] = item.probability
			hits.append(
				SearchHit(
					rank=rank,
					chunk_id=item.chunk_id,
					doc_id=data['doc_id'],
					doc_title=data['doc_title'],
					scope=data['scope'],
					title_path=list(data['title_path']),
					location=location,
					score=item.probability if reranking else item.fused,
					sources=sources,
					text=text[: request.max_chars] if truncated else text,
					truncated=truncated,
					expanded=request.expand_section,
				)
			)
		return hits

	def _section(self, data: dict) -> tuple[str, Location]:
		"""Join adjacent chunks with the same title_path (from haiku.rag). 拼接章节路径相同的相邻段。"""
		chunks = self.store.chunks_for_doc(data['doc_id'])
		position = next(index for index, chunk in enumerate(chunks) if chunk['chunk_id'] == data['chunk_id'])
		start = end = position
		while start > 0 and chunks[start - 1]['title_path'] == data['title_path']:
			start -= 1
		while end + 1 < len(chunks) and chunks[end + 1]['title_path'] == data['title_path']:
			end += 1
		run = chunks[start : end + 1]
		location = run[0]['location']
		for chunk in run[1:]:
			location = location.union(chunk['location'])
		return '\n\n'.join(chunk['text'] for chunk in run), location

	def _reranking_active(self, mode: str | None) -> bool:
		if mode == 'rerank':
			return True
		if mode in ('lexical', 'dense', 'fused'):
			return False
		return self.reranker is not None

	def _result(
		self, request: SearchRequest, hits: list[SearchHit], identifiers: list[str], missing: list[str]
	) -> SearchResult:
		return SearchResult(
			query=request.query,
			stale=request.stale,
			score_kind='calibrated' if self._reranking_active(request.mode) else 'rank_only',
			identifiers=identifiers,
			missing_identifiers=missing,
			results=hits,
		)

def _top_bonus(rank: int) -> float:
	if rank == 1:
		return LEXICAL_TOP_BONUS[0]
	return LEXICAL_TOP_BONUS[1] if rank <= 3 else 0.0

def _present(store: Store, identifier: str) -> bool:
	return any(identifier_hit(run, identifier) for run in store.ident_runs_containing(identifier))

def _rank_by_sources(items: list[_Candidate], name: str) -> dict[int, int]:
	selected = sorted(
		(item for item in items if name in item.sources),
		key=lambda item: (-item.sources[name], item.chunk_id),
	)
	return {item.chunk_pk: rank for rank, item in enumerate(selected, start=1)}
