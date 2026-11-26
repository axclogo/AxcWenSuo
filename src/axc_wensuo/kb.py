"""``KnowledgeBase`` 门面：打开知识库、增量同步、检索、阅读与诊断。

库优先（第 3 节原则）：所有能力先是这里的 Python 函数，命令行与 MCP 只做参数解析与输出格式化。
一个实例对应一个线程（FR-1022），模型只加载一次。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

from axc_wensuo.chunker import CHUNKER_VERSION
from axc_wensuo.config import Config, load_config, resolve_root
from axc_wensuo.dense import Embedder, model_dir_for, model_identity, model_ready, require_model, vector_to_blob
from axc_wensuo.errors import ConfigError, WenSuoRuntimeError
from axc_wensuo.filters import Filters
from axc_wensuo.gaps import list_gaps as list_gaps_impl
from axc_wensuo.gaps import record_gap, should_record
from axc_wensuo.indexer import IndexedChunk, build_chunks
from axc_wensuo.models import (
	ChunkDetail,
	DocsResult,
	DoctorResult,
	Document,
	FailedEntry,
	GapsResult,
	ReadResult,
	ScopeInfo,
	ScopesResult,
	SearchResult,
	SkippedEntry,
	SyncReport,
)
from axc_wensuo.parsers import find_section, parse_file, parser_version_for
from axc_wensuo.parsers.docling import remove_parsed_cache
from axc_wensuo.rerank import Reranker, rerank_model_identity, rerank_ready
from axc_wensuo.search import SearchEngine
from axc_wensuo.source import FileEntry, content_sha256, resolve_scope, scan, stat_changed
from axc_wensuo.store import IndexLock, Store
from axc_wensuo.synonyms import load_synonyms
from axc_wensuo.tokenizers import Tokenizer, get_tokenizer

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[int, int], None]


class KnowledgeBase:
	def __init__(self, root: str | Path, *, config: Config | None = None) -> None:
		self.root = Path(root).expanduser().resolve()
		if not self.root.is_dir():
			raise ConfigError(f'知识库根目录不存在：{self.root}')
		self.config = config or load_config(self.root)
		self._ensure_index_dir()
		self.store = Store(self.config.index_dir)
		self._tokenizer: Tokenizer | None = None
		self._embedder: Embedder | None = None
		self._embedder_signature: tuple[int, int] | None = None
		self._reranker: Reranker | None = None
		self._terms: dict[str, int] = {}
		self._next_term_id = 1
		self._closed = False

	@classmethod
	def open(cls, name: str) -> KnowledgeBase:
		"""按全局配置中的名字打开知识库（FR-101）。"""
		return cls(resolve_root(None, name))

	def _ensure_index_dir(self) -> None:
		try:
			self.config.index_dir.mkdir(parents=True, exist_ok=True)
		except OSError as exc:
			raise ConfigError(
				f'无法创建索引目录 {self.config.index_dir}：{exc}；请在 wensuo.yaml 中把 index_dir 配置到可写位置'
			) from exc

	# ------------------------------------------------------------ 模型

	@property
	def tokenizer(self) -> Tokenizer:
		if self._tokenizer is None:
			self._tokenizer = get_tokenizer(self.config.lexical.tokenizer)
		return self._tokenizer

	def _load_embedder(self, config: Config) -> Embedder:
		if not config.dense.enabled:
			raise WenSuoRuntimeError('向量检索未启用（dense.enabled: false）')
		model_dir = model_dir_for(config.dense, config.models_dir)
		signature = _model_file_signature(model_dir)
		if self._embedder is None or self._embedder.model_dir != model_dir or self._embedder_signature != signature:
			self._embedder = Embedder(model_dir, threads=config.threads)
			self._embedder_signature = signature
		return self._embedder

	def _dense_identity(self, config: Config) -> str | None:
		if not config.dense.enabled:
			return None
		model_dir = require_model(model_dir_for(config.dense, config.models_dir))
		return model_identity(model_dir)

	def _load_reranker(self, config: Config) -> Reranker | None:
		if not config.rerank.enabled:
			return None
		if config.rerank.model_dir is None:
			return None
		if self._reranker is None:
			self._reranker = Reranker(config.rerank.model_dir, threads=config.threads)
		return self._reranker

	@property
	def embedder(self) -> Embedder | None:
		if not self.config.dense.enabled:
			return None
		return self._load_embedder(self.config)

	def model_dir(self) -> Path:
		return model_dir_for(self.config.dense, self.config.models_dir)

	# ------------------------------------------------------------ 同步

	def sync(
		self,
		*,
		threads: int | None = None,
		wait: bool = True,
		progress: ProgressCallback | None = None,
	) -> SyncReport:
		"""显式同步；``ws sync`` 需要等待锁（最多 60 秒，FR-207）。"""
		config = self.config.with_threads(threads)
		lock = IndexLock(config.index_dir)
		if not lock.acquire(60.0 if wait else 0.0):
			raise WenSuoRuntimeError('索引正在被另一个进程写入，等待超时；请稍后重试')
		try:
			return self._sync(config, progress)
		finally:
			lock.release()

	def _auto_sync(self) -> bool:
		"""执行一次自动同步，返回 ``stale`` 标记（FR-207）。"""
		lock = IndexLock(self.config.index_dir)
		if not lock.acquire(0.0):
			if not self.store.has_index():
				raise WenSuoRuntimeError('索引尚未建立，且另一个进程正在同步；请稍后重试')
			return True
		try:
			self._sync(self.config, None)
		finally:
			lock.release()
		return False

	def _chunk_signature(self, config: Config) -> str:
		payload = {
			'chunker': CHUNKER_VERSION,
			'max_chars': config.chunk.max_chars,
			'min_chars': config.chunk.min_chars,
			'tokenizer': self.tokenizer.signature,
			'title_weight': config.lexical.title_weight,
		}
		serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
		return hashlib.sha256(serialized.encode()).hexdigest()

	def _sync(self, config: Config, progress: ProgressCallback | None) -> SyncReport:
		started = time.monotonic()
		if self._tokenizer is None:
			self._tokenizer = get_tokenizer(config.lexical.tokenizer)
		report = SyncReport()
		scan_result = scan(self.root, config)
		scan_map = scan_result.by_id()
		docs = self.store.all_docs()
		report.skipped = [item.doc_id for item in scan_result.skipped]

		chunk_signature = self._chunk_signature(config)
		stored_chunk_signature = self.store.chunk_signature()
		force_rechunk = stored_chunk_signature != chunk_signature
		dense_identity = self._dense_identity(config)
		vector_reset = False
		if config.dense.enabled:
			vector_reset = self.store.dense_signature() != dense_identity or self.store.count_missing_vectors() > 0
			if vector_reset and dense_identity is not None:
				with self.store.transaction():
					self.store.prune_vector_cache(dense_identity)
		force_all = force_rechunk or vector_reset

		deleted = sorted(set(docs) - set(scan_map))
		report.deleted = deleted
		if deleted:
			with self.store.transaction():
				for doc_id in deleted:
					self.store.delete_document(doc_id)
			for doc_id in deleted:
				row = docs[doc_id]
				remove_parsed_cache(config.index_dir, row['sha256'], row['parser'])

		jobs: list[FileEntry] = []
		for entry in scan_result.files:
			doc = docs.get(entry.doc_id)
			if doc is None:
				jobs.append(entry)
				continue
			changed = stat_changed(entry, doc)
			if force_all:
				jobs.append(entry)
				continue
			if not changed:
				continue
			try:
				digest = content_sha256(entry.path)
			except OSError:
				digest = ''
			if digest and digest == doc['sha256']:
				with self.store.transaction():
					self.store.update_doc_stat(entry.doc_id, entry.size, entry.mtime_ns, entry.mtime_ns / 1e9)
				continue
			jobs.append(entry)

		self._terms = self.store.load_terms()
		self._next_term_id = self.store.max_term_id() + 1
		total = len(jobs)
		done = 0
		for entry in jobs:
			doc = docs.get(entry.doc_id)
			if self._failure_cached(entry):
				report.failed.append(entry.doc_id)
				done += 1
				if progress is not None:
					progress(done, total)
				continue
			try:
				self._index_file(entry, config, dense_identity)
			except (ConfigError, WenSuoRuntimeError):
				raise
			except Exception as exc:  # 单个文件失败不影响整库（FR-206）
				logger.warning('解析失败：%s：%s', entry.doc_id, exc)
				self._record_failure(entry, exc)
				# 未提交事务里的新词条需要丢弃，避免后续文档引用不存在的 term id
				self._terms = self.store.load_terms()
				self._next_term_id = self.store.max_term_id() + 1
				report.failed.append(entry.doc_id)
			else:
				if doc is None:
					report.added.append(entry.doc_id)
				else:
					report.modified.append(entry.doc_id)
			done += 1
			if progress is not None:
				progress(done, total)

		with self.store.transaction():
			meta = {
				'chunk_sig': chunk_signature,
				'built_at': dt.datetime.now().isoformat(timespec='seconds'),
				'n_chunks': str(self.store.count_chunks()),
				'avgdl': f'{self.store.avg_dl():.6f}',
			}
			if dense_identity is not None:
				meta['dense_sig'] = dense_identity
			self.store.set_meta_many(meta)
		report.chunks_total = self.store.count_chunks()
		report.seconds = round(time.monotonic() - started, 3)
		return report

	def _failure_cached(self, entry: FileEntry) -> bool:
		failure = self.store.get_failure(entry.doc_id)
		if failure is None:
			return False
		parser_version = parser_version_for(entry.path.suffix)
		if failure['parser_version'] != parser_version:
			return False
		if failure['size'] == entry.size and failure['mtime_ns'] == entry.mtime_ns:
			return True
		try:
			return content_sha256(entry.path) == failure['sha256']
		except OSError:
			return False

	def _record_failure(self, entry: FileEntry, exc: Exception) -> None:
		try:
			digest = content_sha256(entry.path)
		except OSError:
			digest = ''
		with self.store.transaction():
			self.store.delete_document(entry.doc_id)
			self.store.set_failure(
				entry.doc_id,
				str(exc) or exc.__class__.__name__,
				digest,
				parser_version_for(entry.path.suffix),
				entry.size,
				entry.mtime_ns,
			)

	def _index_file(self, entry: FileEntry, config: Config, dense_identity: str | None) -> None:
		parsed = parse_file(entry.path, entry.doc_id, index_dir=config.index_dir)
		for warning in parsed.warnings:
			logger.warning('%s：%s', entry.doc_id, warning)
		scope = resolve_scope(entry.doc_id, parsed.scope_hint, config.scope_rules)
		chunks = build_chunks(
			parsed,
			scope=scope,
			max_chars=config.chunk.max_chars,
			min_chars=config.chunk.min_chars,
			title_weight=config.lexical.title_weight,
			tokenizer=self.tokenizer,
		)
		if config.dense.enabled:
			hashes = [hashlib.sha256(chunk.index_text.encode()).hexdigest() for chunk in chunks]
			cached = self.store.cached_vectors(dense_identity, hashes) if dense_identity else {}
			pending: list[tuple[IndexedChunk, str]] = []
			for chunk, text_sha in zip(chunks, hashes, strict=True):
				known = cached.get(text_sha)
				if known is not None:
					chunk.vec = known
				else:
					pending.append((chunk, text_sha))
			if pending:
				embedder = self._load_embedder(config)
				vectors = embedder.encode_documents([chunk.index_text for chunk, _ in pending])
				cache_rows: list[tuple[str, bytes]] = []
				for (chunk, text_sha), vector in zip(pending, vectors, strict=True):
					chunk.vec = vector_to_blob(vector)
					cache_rows.append((text_sha, chunk.vec))
				if dense_identity is not None:
					with self.store.transaction():
						self.store.store_vectors(dense_identity, cache_rows)
		if not chunks:
			logger.warning('%s：解析结果为空，不产生段', entry.doc_id)
		self._write_document(entry, parsed.doc_title, parsed.tags, parsed.aliases, scope, chunks)

	def _write_document(
		self,
		entry: FileEntry,
		doc_title: str,
		tags: list[str],
		aliases: list[str],
		scope: str,
		chunks: list[IndexedChunk],
	) -> None:
		new_terms: list[tuple[int, str]] = []
		for chunk in chunks:
			for term, _tf_body, _tf_meta in chunk.postings:
				if term not in self._terms:
					self._terms[term] = self._next_term_id
					new_terms.append((self._next_term_id, term))
					self._next_term_id += 1
		modified_ts = entry.mtime_ns / 1e9
		doc_row = {
			'doc_id': entry.doc_id,
			'title': doc_title,
			'scope': scope,
			'tags': json.dumps([unicodedata.normalize('NFC', tag) for tag in tags], ensure_ascii=False),
			'aliases': json.dumps([unicodedata.normalize('NFC', alias) for alias in aliases], ensure_ascii=False),
			'size': entry.size,
			'mtime_ns': entry.mtime_ns,
			'sha256': content_sha256(entry.path),
			'parser': parser_version_for(entry.path.suffix),
			'modified_ts': modified_ts,
			'chunks': len(chunks),
		}
		with self.store.transaction():
			self.store.delete_document(entry.doc_id)
			if new_terms:
				self.store.insert_terms(new_terms)
			self.store.upsert_doc(doc_row)
			for chunk in chunks:
				chunk_pk = self.store.insert_chunk(
					{
						'chunk_id': chunk.chunk_id,
						'doc_id': entry.doc_id,
						'ordinal': chunk.ordinal,
						'scope': scope,
						'title_path': chunk.title_path,
						'text': chunk.text,
						'index_text': chunk.index_text,
						'location': chunk.location.to_dict(),
						'dl': chunk.dl,
						'vec': chunk.vec,
						'modified_ts': modified_ts,
					}
				)
				if chunk.postings:
					self.store.insert_postings(
						[(self._terms[term], chunk_pk, tf_body, tf_meta) for term, tf_body, tf_meta in chunk.postings]
					)
				if chunk.ident_runs:
					self.store.add_ident_runs(chunk_pk, chunk.ident_runs)
			self.store.delete_failure(entry.doc_id)

	# ------------------------------------------------------------ 检索

	def search(
		self,
		query: str,
		*,
		top_k: int = 5,
		scope: list[str] | None = None,
		path_prefix: str | None = None,
		tags: list[str] | None = None,
		file_types: list[str] | None = None,
		modified_after: dt.date | None = None,
		min_score: float | None = None,
		max_chars: int = 800,
		sync: bool = True,
		entry: str = 'api',
		mode: str | None = None,
	) -> SearchResult:
		stale = self._auto_sync() if sync else False
		filters = Filters(
			scope=tuple(scope or ()),
			path_prefix=path_prefix,
			tags=tuple(tags or ()),
			file_types=tuple(file_types or ()),
			modified_after=modified_after,
		)
		engine = self._engine(mode)
		result = engine.search(
			query,
			top_k=top_k,
			filters=filters,
			min_score=min_score,
			max_chars=max_chars,
			mode=mode,
			stale=stale,
		)
		if entry != 'eval' and should_record(
			result_count=len(result.results), missing_identifiers=result.missing_identifiers
		):
			record_gap(
				self.store,
				query=query,
				filters=filters.normalized(),
				entry=entry,
				result_count=len(result.results),
				missing_identifiers=result.missing_identifiers,
			)
		return result

	def _engine(self, mode: str | None) -> SearchEngine:
		need_dense = mode is None or mode in ('dense', 'fused', 'rerank')
		need_rerank = mode is None or mode == 'rerank'
		embedder = self._load_embedder(self.config) if need_dense and self.config.dense.enabled else None
		reranker = self._load_reranker(self.config) if need_rerank else None
		return SearchEngine(
			self.store,
			self.config,
			self.tokenizer,
			embedder=embedder,
			reranker=reranker,
			synonyms=load_synonyms(self.root / 'synonyms.yaml'),
		)

	# ------------------------------------------------------------ 阅读

	def read(
		self,
		doc_id: str,
		*,
		section: str | None = None,
		offset: int = 0,
		max_chars: int = 20000,
		sync: bool = True,
	) -> ReadResult | None:
		if offset < 0:
			raise ConfigError('offset 必须 ≥ 0')
		if max_chars <= 0:
			raise ConfigError('max_chars 必须是正整数')
		doc_id = unicodedata.normalize('NFC', doc_id)
		if sync:
			self._auto_sync()
		row = self.store.get_doc(doc_id)
		if row is None:
			return None
		path = self.root / doc_id
		if not path.is_file():
			# Linux 上文件名可能是 NFD，而 doc_id 统一为 NFC
			fallback = self.root / unicodedata.normalize('NFD', doc_id)
			if not fallback.is_file():
				return None
			path = fallback
		parsed = parse_file(path, doc_id, index_dir=self.config.index_dir)
		text = parsed.text
		if section is not None:
			parts = [part.strip() for part in section.split(' > ')]
			found = find_section(parsed, parts)
			if found is None:
				return None
			start, end = found
			text = text[start:end].rstrip('\n')
		text_slice = text[offset : offset + max_chars]
		next_offset = offset + len(text_slice)
		if next_offset >= len(text):
			next_offset = None
		tags = json.loads(row['tags'])
		return ReadResult(
			doc_id=doc_id,
			doc_title=row['title'],
			scope=row['scope'],
			tags=tags,
			text=text_slice,
			offset=offset,
			next_offset=next_offset,
		)

	def show(self, chunk_id: str) -> ChunkDetail | None:
		row = self.store.get_chunk(chunk_id)
		if row is None:
			return None
		return ChunkDetail(
			chunk_id=row['chunk_id'],
			doc_id=row['doc_id'],
			doc_title=row['doc_title'],
			scope=row['scope'],
			title_path=row['title_path'],
			location=row['location'],
			ordinal=row['ordinal'],
			text=row['text'],
		)

	def list_docs(
		self,
		*,
		scope: list[str] | None = None,
		path_prefix: str | None = None,
		tags: list[str] | None = None,
		file_types: list[str] | None = None,
		modified_after: dt.date | None = None,
		sync: bool = True,
	) -> DocsResult:
		if sync:
			self._auto_sync()
		filters = Filters(
			scope=tuple(scope or ()),
			path_prefix=path_prefix,
			tags=tuple(tags or ()),
			file_types=tuple(file_types or ()),
			modified_after=modified_after,
		).normalized()
		where_sql, params = filters.where('docs')
		rows = self.store.docs_with_filters(where_sql, params)
		documents = [
			Document(
				doc_id=row['doc_id'],
				doc_title=row['title'],
				scope=row['scope'],
				tags=row['tags'],
				chunks=int(row['chunks']),
				modified=_format_modified(row['modified_ts']),
			)
			for row in rows
		]
		return DocsResult(docs=documents)

	def list_scopes(self, *, sync: bool = True) -> ScopesResult:
		if sync:
			self._auto_sync()
		stats = dict(self.store.scope_stats())
		names = sorted(set(stats) | set(self.config.scopes))
		scopes = [
			ScopeInfo(scope=name, description=self.config.scopes.get(name), docs=stats.get(name, 0)) for name in names
		]
		return ScopesResult(scopes=scopes)

	# ------------------------------------------------------------ 诊断与缺口

	def doctor(self) -> DoctorResult:
		stats = self.store.stats()
		scan_result = scan(self.root, self.config)
		dense_dir = model_dir_for(self.config.dense, self.config.models_dir)
		dense_ready = model_ready(dense_dir)
		dense = {
			'enabled': self.config.dense.enabled,
			'model': self.config.dense.model,
			'ready': dense_ready,
			'identity': model_identity(dense_dir) if dense_ready else None,
		}
		rerank_dir = self.config.rerank.model_dir
		rerank_info: dict[str, Any] = {
			'enabled': self.config.rerank.enabled,
			'model_dir': str(rerank_dir) if rerank_dir else None,
			'ready': rerank_ready(rerank_dir) if rerank_dir else False,
			'identity': rerank_model_identity(rerank_dir) if rerank_dir and rerank_ready(rerank_dir) else None,
		}
		failed = [FailedEntry(doc_id=row['doc_id'], reason=row['reason']) for row in self.store.list_failures()]
		skipped = [SkippedEntry(doc_id=item.doc_id, reason=item.reason) for item in scan_result.skipped]
		return DoctorResult(
			root=str(self.root),
			index_dir=str(self.config.index_dir),
			config_files=[str(path) for path in self.config.config_files],
			docs=stats['docs'],
			chunks=stats['chunks'],
			scopes=dict(self.store.scope_stats()),
			file_types=self.store.file_type_stats(),
			skipped=skipped,
			failed=failed,
			dense=dense,
			rerank=rerank_info,
			index_bytes=_directory_size(self.config.index_dir),
		)

	def evaluate(self, queries_path: str | Path, modes: list[str], *, k: int = 5) -> Any:
		"""执行 ``ws eval``（指标计算在 :mod:`axc_wensuo.evaluate`）。"""
		from axc_wensuo.evaluate import evaluate as run_evaluate
		from axc_wensuo.evaluate import load_queries

		self._auto_sync()
		queries = load_queries(queries_path, default_k=k)

		def search(query: str, top_k: int, mode: str) -> SearchResult:
			engine = self._engine(mode)
			return engine.search(query, top_k=top_k, mode=mode, max_chars=1, stale=False)

		return run_evaluate(search, queries, modes, k=k)

	def gaps(self, *, limit: int = 50) -> GapsResult:
		return list_gaps_impl(self.store, limit)

	def clear_gaps(self) -> int:
		return self.store.clear_gaps()

	# ------------------------------------------------------------ 生命周期

	def close(self) -> None:
		if not self._closed:
			self.store.close()
			self._closed = True

	def __enter__(self) -> KnowledgeBase:
		return self

	def __exit__(self, *exc_info: object) -> None:
		self.close()


def _model_file_signature(model_dir: Path) -> tuple[int, int] | None:
	path = Path(model_dir) / 'model.onnx'
	try:
		stat = path.stat()
	except OSError:
		return None
	return (stat.st_mtime_ns, stat.st_size)


def _format_modified(timestamp: float) -> str:
	return dt.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')


def _directory_size(directory: Path) -> int:
	total = 0
	for path in directory.rglob('*'):
		try:
			if path.is_file():
				total += path.stat().st_size
		except OSError:
			continue
	return total


def open_knowledge_base(root: str | Path | None = None, *, kb: str | None = None) -> KnowledgeBase:
	"""按 ``--root`` / ``--kb`` 打开知识库（命令行与 MCP 共用）。"""
	return KnowledgeBase(resolve_root(str(root) if root else None, kb))


__all__ = ['KnowledgeBase', 'open_knowledge_base']
