"""检索流水线（第 7.3 节）：过滤 → 双路召回 → 加权 RRF → 标识符分层 → 可选重排 → 阈值 →
每文档限段 → 截断 top_k。

所有排序在分数相同时按 ``chunk_id`` 升序打破平局，保证确定性（第 3 节原则）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from axc_wensuo.config import Config
from axc_wensuo.dense import DenseIndex, Embedder
from axc_wensuo.errors import ConfigError
from axc_wensuo.filters import Filters
from axc_wensuo.lexical import LexicalIndex
from axc_wensuo.models import SearchHit, SearchResult
from axc_wensuo.rerank import Reranker
from axc_wensuo.store import Store
from axc_wensuo.synonyms import Synonyms
from axc_wensuo.tokenizers import Tokenizer, extract_identifiers, identifier_in_runs

MODES = ('lexical', 'dense', 'fused', 'rerank')
RRF_K = 60
MAX_QUERY_CHARS = 1000


@dataclass
class _Candidate:
	chunk_pk: int
	chunk_id: str
	data: dict
	sources: dict[str, float] = field(default_factory=dict)
	fused: float = 0.0
	layer: int = 0
	final: float = 0.0
	final_probability: float = 0.0
	rank: int = 0


class SearchEngine:
	def __init__(
		self,
		store: Store,
		config: Config,
		tokenizer: Tokenizer,
		*,
		embedder: Embedder | None = None,
		reranker: Reranker | None = None,
		synonyms: Synonyms | None = None,
	) -> None:
		self.store = store
		self.config = config
		self.tokenizer = tokenizer
		self.embedder = embedder
		self.reranker = reranker
		self.synonyms = synonyms or Synonyms([])
		self.lexical = LexicalIndex(store, config.lexical, tokenizer)
		self.dense = DenseIndex(store) if embedder is not None else None

	def search(
		self,
		query: str,
		*,
		top_k: int = 5,
		filters: Filters | None = None,
		min_score: float | None = None,
		max_chars: int = 800,
		mode: str | None = None,
		stale: bool = False,
	) -> SearchResult:
		if len(query) > MAX_QUERY_CHARS:
			raise ConfigError(f'查询最长 {MAX_QUERY_CHARS} 个码点，当前 {len(query)} 个')
		if top_k <= 0:
			raise ConfigError('top_k 必须是正整数')
		if max_chars <= 0:
			raise ConfigError('max_chars 必须是正整数')
		if mode is not None and mode not in MODES:
			raise ConfigError(f'未知检索模式：{mode}；可选：{", ".join(MODES)}')
		if mode == 'rerank' and self.reranker is None:
			raise ConfigError('未配置重排模型，不能使用 rerank 模式')
		if mode == 'dense' and self.embedder is None:
			raise ConfigError('向量检索未启用，不能使用 dense 模式')

		active_filters = (filters or Filters()).normalized()
		tokens = self.tokenizer.tokenize(query)
		if self.synonyms:
			tokens.extend(self.synonyms.expanded_tokens(query, self.tokenizer))
			tokens = list(dict.fromkeys(tokens))
		if not tokens:
			raise ConfigError('查询分词后没有任何词')
		identifiers = extract_identifiers(query)
		missing_identifiers = [identifier for identifier in identifiers if not self.store.ident_run_exists(identifier)]

		reranking = self._reranking_active(mode)
		if not reranking and min_score is not None:
			raise ConfigError('未开启重排时不能使用 min_score 阈值（分数只能比较先后）')
		if min_score is not None and not 0 <= min_score <= 1:
			raise ConfigError('min_score 必须在 [0, 1] 内')

		candidates: dict[int, _Candidate] = {}
		use_lexical = mode in (None, 'lexical', 'fused', 'rerank')
		use_dense = mode in (None, 'dense', 'fused', 'rerank') and self.embedder is not None
		if use_lexical:
			self._add_lexical(candidates, tokens, active_filters)
		if use_dense:
			self._add_dense(candidates, query, active_filters)

		items = list(candidates.values())
		data_map = self.store.chunks_by_pks([item.chunk_pk for item in items])
		items = [item for item in items if item.chunk_pk in data_map]
		for item in items:
			item.data = data_map[item.chunk_pk]
		if not items:
			return SearchResult(
				query=query,
				stale=stale,
				score_kind='calibrated' if reranking else 'rank_only',
				identifiers=identifiers,
				missing_identifiers=missing_identifiers,
				results=[],
			)
		self._fuse(items)
		self._layers(items, identifiers)
		items.sort(key=lambda item: (-item.layer, -item.fused, item.chunk_id))
		for rank, item in enumerate(items, start=1):
			item.rank = rank

		if reranking:
			items = self._apply_rerank(query, items, min_score)
			items.sort(key=lambda item: (-item.layer, -item.final, item.chunk_id))
		else:
			items.sort(key=lambda item: (-item.layer, -item.fused, item.chunk_id))

		items = self._limit_per_doc(items)
		items = items[:top_k]
		results: list[SearchHit] = []
		for rank, item in enumerate(items, start=1):
			data = item.data
			text = data['text']
			truncated = len(text) > max_chars
			sources: dict[str, float] = dict(item.sources)
			if reranking:
				sources['rerank'] = item.final_probability
			results.append(
				SearchHit(
					rank=rank,
					chunk_id=item.chunk_id,
					doc_id=data['doc_id'],
					doc_title=data['doc_title'],
					scope=data['scope'],
					title_path=list(data['title_path']),
					location=data['location'],
					score=item.final_probability if reranking else item.fused,
					sources=sources,
					text=text[:max_chars] if truncated else text,
					truncated=truncated,
				)
			)
		return SearchResult(
			query=query,
			stale=stale,
			score_kind='calibrated' if reranking else 'rank_only',
			identifiers=identifiers,
			missing_identifiers=missing_identifiers,
			results=results,
		)

	def _reranking_active(self, mode: str | None) -> bool:
		if mode == 'rerank':
			return True
		if mode in ('lexical', 'dense', 'fused'):
			return False
		return self.reranker is not None

	def _add_lexical(self, candidates: dict[int, _Candidate], tokens: list[str], filters: Filters) -> None:
		for hit in self.lexical.search(tokens, filters, self.config.fusion.candidates):
			candidate = candidates.setdefault(hit.chunk_pk, _Candidate(hit.chunk_pk, hit.chunk_id, {}))
			candidate.sources['lexical'] = float(hit.score)

	def _add_dense(self, candidates: dict[int, _Candidate], query: str, filters: Filters) -> None:
		assert self.embedder is not None and self.dense is not None
		query_vector = self.embedder.encode_query(query)
		for hit in self.dense.search(query_vector, filters, self.config.fusion.candidates):
			candidate = candidates.setdefault(hit.chunk_pk, _Candidate(hit.chunk_pk, hit.chunk_id, {}))
			candidate.sources['dense'] = float(hit.score)

	def _fuse(self, items: list[_Candidate]) -> None:
		lexical_rank = {
			item.chunk_pk: rank
			for rank, item in enumerate(
				sorted(
					(item for item in items if 'lexical' in item.sources),
					key=lambda item: (-item.sources['lexical'], item.chunk_id),
				),
				start=1,
			)
		}
		dense_rank = {
			item.chunk_pk: rank
			for rank, item in enumerate(
				sorted(
					(item for item in items if 'dense' in item.sources),
					key=lambda item: (-item.sources['dense'], item.chunk_id),
				),
				start=1,
			)
		}
		weights = self.config.fusion
		for item in items:
			score = 0.0
			if item.chunk_pk in lexical_rank:
				score += weights.w_lexical / (RRF_K + lexical_rank[item.chunk_pk])
			if item.chunk_pk in dense_rank:
				score += weights.w_dense / (RRF_K + dense_rank[item.chunk_pk])
			item.fused = score
			# sources 只保留名次，符合 8.3 节示例 {"lexical": 1, "dense": 4}
			item.sources = {}
			if item.chunk_pk in lexical_rank:
				item.sources['lexical'] = lexical_rank[item.chunk_pk]
			if item.chunk_pk in dense_rank:
				item.sources['dense'] = dense_rank[item.chunk_pk]

	def _layers(self, items: list[_Candidate], identifiers: list[str]) -> None:
		if not identifiers:
			for item in items:
				item.layer = 0
			return
		runs = self.store.ident_runs_for_chunks([item.chunk_pk for item in items])
		for item in items:
			chunk_runs = runs.get(item.chunk_pk, [])
			item.layer = sum(1 for identifier in identifiers if identifier_in_runs(chunk_runs, identifier))

	def _apply_rerank(self, query: str, items: list[_Candidate], min_score: float | None) -> list[_Candidate]:
		assert self.reranker is not None
		top_k = self.config.rerank.top_k
		selected = items[:top_k]
		if not selected:
			return []
		passages = [(item.data['title_path'], item.data['text']) for item in selected]
		probabilities = self.reranker.score(query, passages)
		if min_score is not None:
			threshold = min_score
		elif self.config.rerank.min_score is not None:
			threshold = self.config.rerank.min_score
		else:
			threshold = self.reranker.meta.default_min_score
		kept: list[_Candidate] = []
		for item, probability in zip(selected, probabilities, strict=True):
			item.final_probability = probability
			if probability < threshold:
				continue
			weight = 0.75 if item.rank <= 3 else 0.60 if item.rank <= 10 else 0.40
			s_ret = 1 - (item.rank - 1) / top_k
			item.final = weight * s_ret + (1 - weight) * probability
			kept.append(item)
		return kept

	def _limit_per_doc(self, items: list[_Candidate]) -> list[_Candidate]:
		limit = self.config.fusion.max_per_doc
		counts: dict[str, int] = {}
		kept: list[_Candidate] = []
		for item in items:
			doc_id = item.data['doc_id']
			count = counts.get(doc_id, 0)
			if count >= limit:
				continue
			counts[doc_id] = count + 1
			kept.append(item)
		return kept
