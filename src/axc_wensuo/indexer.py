"""Build index rows from a parsed document. 从解析结果构建索引数据。"""

from __future__ import annotations

from dataclasses import dataclass, field

from axc_wensuo.chunker import chunk_document
from axc_wensuo.models import Location
from axc_wensuo.parsers.base import ParsedDocument
from axc_wensuo.tokenizers import Tokenizer, extract_ident_runs


@dataclass(frozen=True)
class IndexSettings:
	"""Chunking and lexical settings. 切段与关键词设置。"""

	max_chars: int
	min_chars: int
	title_weight: float
	tokenizer: Tokenizer

@dataclass
class IndexedChunk:
	"""One chunk ready for storage. 一个可入库的段。"""

	chunk_id: str
	ordinal: int
	text: str
	index_text: str
	title_path: list[str]
	location: Location
	dl: float
	postings: list[tuple[str, int, int]] = field(default_factory=list)
	ident_runs: list[str] = field(default_factory=list)
	vec: bytes | None = None

def build_chunks(doc: ParsedDocument, scope: str, settings: IndexSettings) -> list[IndexedChunk]:
	"""Chunk one document and compute lexical data. 切段并计算词频。"""
	drafts = chunk_document(
		doc,
		scope=scope,
		doc_title=doc.doc_title,
		max_chars=settings.max_chars,
		min_chars=settings.min_chars,
	)
	aliases = '\n'.join(doc.aliases)
	chunks: list[IndexedChunk] = []
	for draft in drafts:
		meta_text = f'{doc.doc_title}\n{aliases}\n{" > ".join(draft.title_path)}'
		body_counts = settings.tokenizer.counts(draft.text)
		meta_counts = settings.tokenizer.counts(meta_text)
		postings: list[tuple[str, int, int]] = []
		length = 0.0
		for term, count in body_counts.items():
			meta_count = meta_counts.get(term, 0)
			postings.append((term, count, meta_count))
			length += count + settings.title_weight * meta_count
		for term, meta_count in meta_counts.items():
			if term not in body_counts:
				postings.append((term, 0, meta_count))
				length += settings.title_weight * meta_count
		chunks.append(
			IndexedChunk(
				chunk_id=draft.chunk_id,
				ordinal=draft.ordinal,
				text=draft.text,
				index_text=draft.index_text,
				title_path=list(draft.title_path),
				location=draft.location,
				dl=length,
				postings=postings,
				ident_runs=extract_ident_runs(draft.index_text),
			)
		)
	return chunks
