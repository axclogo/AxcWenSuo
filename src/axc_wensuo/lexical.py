"""BM25 lexical index. BM25 关键词索引。"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from axc_wensuo.config import LexicalConfig
from axc_wensuo.filters import Filters
from axc_wensuo.store import Store
from axc_wensuo.tokenizers import Tokenizer


@dataclass(frozen=True)
class LexicalHit:
	chunk_pk: int
	chunk_id: str
	score: float

class LexicalIndex:
	def __init__(self, store: Store, config: LexicalConfig, tokenizer: Tokenizer) -> None:
		self.store = store
		self.config = config
		self.tokenizer = tokenizer

	def term_frequencies(self, text: str, meta_text: str) -> tuple[Counter[str], Counter[str]]:
		"""Count body and meta tokens separately. 分别统计正文与元信息词频。"""
		return self.tokenizer.counts(text), self.tokenizer.counts(meta_text)

	def weighted_length(self, body: Counter[str], meta: Counter[str]) -> float:
		weight = self.config.title_weight
		return float(sum(body.values()) + weight * sum(meta.values()))

	def search(self, query_tokens: list[str], filters: Filters, limit: int) -> list[LexicalHit]:
		if not query_tokens:
			return []
		store = self.store
		total_chunks = store.count_chunks()
		if total_chunks == 0:
			return []
		avgdl = store.avg_dl()
		if avgdl <= 0:
			return []
		terms = list(dict.fromkeys(query_tokens))
		term_ids = store.term_ids(terms)
		if not term_ids:
			return []
		where_sql, params = filters.where('c')
		scores: dict[int, float] = {}
		chunk_ids: dict[int, str] = {}
		k1 = self.config.k1
		b = self.config.b
		for token in terms:
			term_id = term_ids.get(token)
			if term_id is None:
				continue
			df = store.term_df(term_id)
			if df == 0:
				continue
			idf = math.log(1 + (total_chunks - df + 0.5) / (df + 0.5))
			for row in store.postings_with_filters(term_id, where_sql, params):
				chunk_pk = int(row['chunk_pk'])
				tf = row['tf_body'] + self.config.title_weight * row['tf_meta']
				if tf <= 0:
					continue
				denominator = tf + k1 * (1 - b + b * row['dl'] / avgdl)
				scores[chunk_pk] = scores.get(chunk_pk, 0.0) + idf * tf * (k1 + 1) / denominator
				chunk_ids[chunk_pk] = row['chunk_id']
		ranked = sorted(scores.items(), key=lambda item: (-item[1], chunk_ids[item[0]]))
		return [
			LexicalHit(chunk_pk=pk, chunk_id=chunk_ids[pk], score=score) for pk, score in ranked[:limit] if score > 0
		]
