"""KnowledgeBase facade: sync, search, read, diagnostics. 知识库门面。"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import sqlite3
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import wraps
from pathlib import Path
from typing import Any

from axc_wensuo.chunker import CHUNKER_VERSION
from axc_wensuo.config import SYNONYMS_NAME, Config, load_config, resolve_root
from axc_wensuo.dense import Embedder, model_dir_for, model_identity, model_ready, require_model, vector_to_blob
from axc_wensuo.errors import ConfigError, WenSuoRuntimeError
from axc_wensuo.filters import Filters
from axc_wensuo.gaps import GapQuery, record_gap, should_record
from axc_wensuo.gaps import list_gaps as list_gaps_impl
from axc_wensuo.hashing import file_sha256
from axc_wensuo.indexer import IndexedChunk, IndexSettings, build_chunks
from axc_wensuo.models import (
	ChunkDetail,
	DocsResult,
	DoctorResult,
	Document,
	EvalResult,
	FailedEntry,
	GapsResult,
	ReadResult,
	ScopeInfo,
	ScopesResult,
	SearchResult,
	SkippedEntry,
	SyncReport,
)
from axc_wensuo.parsers import ParseError, find_section, parse_file, parser_version_for, remove_parsed_cache
from axc_wensuo.rerank import Reranker, rerank_model_identity, rerank_ready
from axc_wensuo.search import SearchEngine, SearchRequest, SearchResources
from axc_wensuo.source import FileEntry, resolve_scope, scan, stat_changed
from axc_wensuo.store import FailureRecord, IndexLock, Store
from axc_wensuo.synonyms import load_synonyms
from axc_wensuo.tokenizers import Tokenizer, get_tokenizer

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class SearchOptions:
	"""Search extras. 检索附加参数。"""

	filters: Filters = field(default_factory=Filters)
	min_score: float | None = None
	max_chars: int = 800
	sync: bool = True
	entry: str = 'api'
	mode: str | None = None
	expand_section: bool = False

@dataclass(frozen=True)
class ReadOptions:
	"""Read offsets and sync policy. 阅读偏移与同步策略。"""

	offset: int = 0
	max_chars: int = 20000
	sync: bool = True

@dataclass(frozen=True)
class ListDocsOptions:
	"""Document listing extras. 文档列表附加参数。"""

	filters: Filters = field(default_factory=Filters)
	sync: bool = True

@dataclass(frozen=True)
class _DocumentPayload:
	"""A parsed document ready for storage. 待入库的解析结果。"""

	title: str
	tags: tuple[str, ...]
	aliases: tuple[str, ...]
	scope: str
	chunks: list[IndexedChunk]

@dataclass
class _SyncState:
	"""State passed through one sync. 一次同步的上下文。"""

	config: Config
	chunk_signature: str
	dense_identity: str | None
	report: SyncReport
	progress: ProgressCallback | None

def _runtime_errors(method):
	"""Translate low-level failures. 转换底层异常为运行错误。"""
	@wraps(method)
	def wrapper(*args, **kwargs):
		try:
			return method(*args, **kwargs)
		except (OSError, sqlite3.Error, ParseError) as exc:
			raise WenSuoRuntimeError(f'{type(exc).__name__}: {exc}') from exc
	return wrapper

class KnowledgeBase:
	"""Local knowledge base over one folder. 一个目录对应的本地知识库。"""

	def __init__(self, root: str | Path, *, config: Config | None = None) -> None:
		self.root = Path(root).expanduser().resolve()
		if not self.root.is_dir():
			raise ConfigError(f'知识库根目录不存在：{self.root}')
		self.config = config or load_config(self.root)
		self._ensure_index_dir()
		try:
			self.store = Store(self.config.index_dir)
		except (OSError, sqlite3.Error) as exc:
			raise WenSuoRuntimeError(f'无法打开索引：{exc}') from exc
		self._tokenizer: Tokenizer | None = None
		self._embedder: Embedder | None = None
		self._embedder_signature: tuple[int, int] | None = None
		self._reranker: Reranker | None = None
		self._terms: dict[str, int] = {}
		self._next_term_id = 1
		self._closed = False

	@classmethod
	def open(cls, name: str) -> KnowledgeBase:
		"""Open a named knowledge base. 按名字打开知识库。"""
		return cls(resolve_root(None, name))

	@property
	def tokenizer(self) -> Tokenizer:
		if self._tokenizer is None:
			self._tokenizer = get_tokenizer(self.config.lexical.tokenizer)
		return self._tokenizer

	def _ensure_index_dir(self) -> None:
		try:
			self.config.index_dir.mkdir(parents=True, exist_ok=True)
		except OSError as exc:
			raise ConfigError(f'无法创建索引目录 {self.config.index_dir}：{exc}；请配置可写的 index_dir') from exc

	def _load_embedder(self, config: Config) -> Embedder:
		model_dir = model_dir_for(config.dense, config.models_dir)
		signature = _model_file_signature(model_dir)
		if self._embedder is None or self._embedder.model_dir != model_dir or self._embedder_signature != signature:
			self._embedder = Embedder(model_dir, threads=config.threads)
			self._embedder_signature = signature
		return self._embedder

	def _dense_identity(self, config: Config) -> str | None:
		if not config.dense.enabled:
			return None
		return model_identity(require_model(model_dir_for(config.dense, config.models_dir)))

	def _load_reranker(self, config: Config) -> Reranker | None:
		if config.rerank.model_dir is None:
			return None
		if self._reranker is None:
			self._reranker = Reranker(config.rerank.model_dir, threads=config.threads)
		return self._reranker

	def model_dir(self) -> Path:
		return model_dir_for(self.config.dense, self.config.models_dir)

	@_runtime_errors
	def sync(
		self,
		*,
		threads: int | None = None,
		wait: bool = True,
		progress: ProgressCallback | None = None,
	) -> SyncReport:
		"""Run an explicit sync. 执行显式同步。"""
		config = self.config.with_threads(threads)
		lock = IndexLock(config.index_dir)
		if not lock.acquire(60.0 if wait else 0.0):
			raise WenSuoRuntimeError('索引正在被另一个进程写入，等待超时；请稍后重试')
		try:
			return self._sync(config, progress)
		finally:
			lock.release()

	def _auto_sync(self) -> bool:
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

	def _sync(self, config: Config, progress: ProgressCallback | None) -> SyncReport:
		started = time.monotonic()
		scan_result = scan(self.root, config)
		docs = self.store.all_docs()
		present = set(scan_result.by_id())
		deleted = sorted(set(docs) - present)
		report = SyncReport(deleted=deleted, skipped=[item.doc_id for item in scan_result.skipped])
		self._delete_documents(deleted, docs)
		self._drop_stale_failures(present)
		chunk_signature = self._chunk_signature(config)
		dense_identity, force_all = self._reindex_plan(config, chunk_signature)
		jobs = self._collect_jobs(scan_result.files, docs, force_all)
		self._load_terms()
		state = _SyncState(config, chunk_signature, dense_identity, report, progress)
		self._index_jobs(jobs, docs, state)
		self._finish_sync(state, started)
		return report

	def _chunk_signature(self, config: Config) -> str:
		payload = {
			'chunker': CHUNKER_VERSION,
			'max_chars': config.chunk.max_chars,
			'min_chars': config.chunk.min_chars,
			'tokenizer': self.tokenizer.signature,
			'title_weight': config.lexical.title_weight,
			'scope_rules': [[rule.glob, rule.scope] for rule in config.scope_rules],
		}
		return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

	def _reindex_plan(self, config: Config, chunk_signature: str) -> tuple[str | None, bool]:
		force_rechunk = self.store.chunk_signature() != chunk_signature
		dense_identity = self._dense_identity(config)
		vector_reset = False
		if config.dense.enabled:
			vector_reset = self.store.dense_signature() != dense_identity or self.store.count_missing_vectors() > 0
			if vector_reset and dense_identity is not None:
				with self.store.transaction():
					self.store.prune_vector_cache(dense_identity)
		return dense_identity, force_rechunk or vector_reset

	def _delete_documents(self, deleted: list[str], docs: dict[str, dict]) -> None:
		if not deleted:
			return
		with self.store.transaction():
			for doc_id in deleted:
				self.store.delete_document(doc_id)
		for doc_id in deleted:
			self._release_parsed_cache(docs[doc_id])

	def _drop_stale_failures(self, present: set[str]) -> None:
		"""Forget failures of files that are gone or excluded (FR-203). 删除已不存在或被排除文件的失败记录。"""
		stale = sorted(self.store.failure_ids() - present)
		if stale:
			with self.store.transaction():
				for doc_id in stale:
					self.store.delete_failure(doc_id)

	def _collect_jobs(self, files: list[FileEntry], docs: dict[str, dict], force_all: bool) -> list[FileEntry]:
		jobs: list[FileEntry] = []
		for entry in files:
			doc = docs.get(entry.doc_id)
			if doc is None or force_all:
				jobs.append(entry)
				continue
			if not stat_changed(entry, doc):
				continue
			if self._same_content(entry, doc['sha256']):
				with self.store.transaction():
					self.store.update_doc_stat(entry.doc_id, entry.size, entry.mtime_ns, entry.mtime_ns / 1e9)
				continue
			jobs.append(entry)
		return jobs

	def _load_terms(self) -> None:
		self._terms = self.store.load_terms()
		self._next_term_id = self.store.max_term_id() + 1

	def _index_jobs(self, jobs: list[FileEntry], docs: dict[str, dict], state: _SyncState) -> None:
		total = len(jobs)
		for done, entry in enumerate(jobs, start=1):
			self._index_one(entry, docs.get(entry.doc_id), state)
			if state.progress is not None:
				state.progress(done, total)

	def _same_content(self, entry: FileEntry, sha256: str) -> bool:
		"""Compare the content hash; unreadable files count as changed. 比较内容哈希；读不了的文件按已变化处理。"""
		try:
			return file_sha256(entry.path) == sha256
		except OSError:
			return False

	def _index_one(self, entry: FileEntry, doc: dict | None, state: _SyncState) -> None:
		if self._failure_cached(entry):
			state.report.failed.append(entry.doc_id)
			return
		try:
			self._index_file(entry, state.config, state.dense_identity)
		except ParseError as exc:
			logger.warning('解析失败：%s：%s', entry.doc_id, exc)
			self._record_failure(entry, exc)
			state.report.failed.append(entry.doc_id)
		else:
			(state.report.added if doc is None else state.report.modified).append(entry.doc_id)
		if doc is not None:
			self._release_parsed_cache(doc)

	def _release_parsed_cache(self, old: dict) -> None:
		"""Drop a parse cache no document uses any more. 删除不再被任何文档使用的解析缓存。"""
		if not self.store.parsed_cache_in_use(old['sha256'], old['parser']):
			remove_parsed_cache(self.config.index_dir, old['sha256'], old['parser'])

	def _failure_cached(self, entry: FileEntry) -> bool:
		"""Is the failure still valid? Stat first, then hash. 失败记录是否仍有效：先比大小与时间，再比哈希。"""
		failure = self.store.get_failure(entry.doc_id)
		version = parser_version_for(entry.path.suffix)
		if failure is None or not failure['sha256'] or failure['parser_version'] != version:
			return False
		if (entry.size, entry.mtime_ns) == (failure['size'], failure['mtime_ns']):
			return True
		if not self._same_content(entry, failure['sha256']):
			return False
		with self.store.transaction():
			self.store.update_failure_stat(entry.doc_id, entry.size, entry.mtime_ns)
		return True

	def _record_failure(self, entry: FileEntry, exc: ParseError) -> None:
		try:
			digest = file_sha256(entry.path)
		except OSError:
			digest = ''  # Unreadable: always retried. 读不了的文件每次同步都重试。
		record = FailureRecord(
			doc_id=entry.doc_id,
			reason=str(exc),
			sha256=digest,
			parser_version=parser_version_for(entry.path.suffix),
			size=entry.size,
			mtime_ns=entry.mtime_ns,
		)
		with self.store.transaction():
			self.store.delete_document(entry.doc_id)
			self.store.set_failure(record)

	def _index_file(self, entry: FileEntry, config: Config, dense_identity: str | None) -> None:
		parsed = parse_file(entry.path, entry.doc_id, index_dir=config.index_dir)
		for warning in parsed.warnings:
			logger.warning('%s：%s', entry.doc_id, warning)
		scope = resolve_scope(entry.doc_id, parsed.scope_hint, config.scope_rules)
		settings = IndexSettings(
			config.chunk.max_chars,
			config.chunk.min_chars,
			config.lexical.title_weight,
			self.tokenizer,
		)
		chunks = build_chunks(parsed, scope, settings)
		if config.dense.enabled:
			self._assign_vectors(chunks, config, dense_identity)
		if not chunks:
			logger.warning('%s：解析结果为空，不产生段', entry.doc_id)
		payload = _DocumentPayload(parsed.doc_title, tuple(parsed.tags), tuple(parsed.aliases), scope, chunks)
		self._write_document(entry, payload)

	def _assign_vectors(self, chunks: list[IndexedChunk], config: Config, dense_identity: str | None) -> None:
		hashes = [hashlib.sha256(chunk.index_text.encode()).hexdigest() for chunk in chunks]
		cached = self.store.cached_vectors(dense_identity, hashes) if dense_identity else {}
		pending: list[tuple[IndexedChunk, str]] = []
		for chunk, text_sha in zip(chunks, hashes, strict=True):
			known = cached.get(text_sha)
			if known is not None:
				chunk.vec = known
			else:
				pending.append((chunk, text_sha))
		if not pending:
			return
		embedder = self._load_embedder(config)
		vectors = embedder.encode_documents([chunk.index_text for chunk, _ in pending])
		cache_rows: list[tuple[str, bytes]] = []
		for (chunk, text_sha), vector in zip(pending, vectors, strict=True):
			chunk.vec = vector_to_blob(vector)
			cache_rows.append((text_sha, chunk.vec))
		if dense_identity is not None:
			with self.store.transaction():
				self.store.store_vectors(dense_identity, cache_rows)

	def _write_document(self, entry: FileEntry, payload: _DocumentPayload) -> None:
		new_terms = self._assign_terms(payload.chunks)
		doc_row = self._doc_row(entry, payload)
		modified_ts = entry.mtime_ns / 1e9
		with self.store.transaction():
			self.store.delete_document(entry.doc_id)
			if new_terms:
				self.store.insert_terms(new_terms)
			self.store.upsert_doc(doc_row)
			self._insert_chunks(entry.doc_id, payload.scope, payload.chunks, modified_ts)
			self.store.delete_failure(entry.doc_id)

	def _assign_terms(self, chunks: list[IndexedChunk]) -> list[tuple[int, str]]:
		new_terms: list[tuple[int, str]] = []
		for chunk in chunks:
			for term, _tf_body, _tf_meta in chunk.postings:
				if term not in self._terms:
					self._terms[term] = self._next_term_id
					new_terms.append((self._next_term_id, term))
					self._next_term_id += 1
		return new_terms

	def _doc_row(self, entry: FileEntry, payload: _DocumentPayload) -> dict[str, Any]:
		relative_path = entry.path.relative_to(self.root).as_posix()
		return {
			'doc_id': entry.doc_id,
			'path': relative_path,
			'title': payload.title,
			'scope': payload.scope,
			'tags': json.dumps(_normalize_all(payload.tags), ensure_ascii=False),
			'aliases': json.dumps(_normalize_all(payload.aliases), ensure_ascii=False),
			'size': entry.size,
			'mtime_ns': entry.mtime_ns,
			'sha256': file_sha256(entry.path),
			'parser': parser_version_for(entry.path.suffix),
			'modified_ts': entry.mtime_ns / 1e9,
			'chunks': len(payload.chunks),
		}

	def _insert_chunks(self, doc_id: str, scope: str, chunks: list[IndexedChunk], modified_ts: float) -> None:
		for chunk in chunks:
			chunk_pk = self.store.insert_chunk(
				{
					'chunk_id': chunk.chunk_id,
					'doc_id': doc_id,
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

	def _finish_sync(self, state: _SyncState, started: float) -> None:
		with self.store.transaction():
			meta = {
				'chunk_sig': state.chunk_signature,
				'built_at': dt.datetime.now().isoformat(timespec='seconds'),
				'n_chunks': str(self.store.count_chunks()),
				'avgdl': f'{self.store.avg_dl():.6f}',
			}
			if state.dense_identity is not None:
				meta['dense_sig'] = state.dense_identity
			self.store.set_meta_many(meta)
		state.report.chunks_total = self.store.count_chunks()
		state.report.seconds = round(time.monotonic() - started, 3)

	@_runtime_errors
	def search(
		self,
		query: str,
		*,
		top_k: int = 5,
		scope: list[str] | None = None,
		options: SearchOptions | None = None,
	) -> SearchResult:
		"""Search with optional filters. 检索，可带过滤条件。"""
		opts = options or SearchOptions()
		filters = replace(opts.filters, scope=tuple(scope)) if scope else opts.filters
		stale = self._auto_sync() if opts.sync else False
		request = SearchRequest(
			query, top_k, filters.normalized(), opts.min_score, opts.max_chars, opts.mode, stale, opts.expand_section
		)
		result = self._engine(opts.mode).search(request)
		if opts.entry != 'eval' and should_record(
			result_count=len(result.results), missing_identifiers=result.missing_identifiers
		):
			record_gap(
				self.store,
				GapQuery(query, filters.normalized(), opts.entry, len(result.results), result.missing_identifiers),
			)
		return result

	def _engine(self, mode: str | None) -> SearchEngine:
		need_dense = mode is None or mode in ('dense', 'fused', 'rerank')
		need_rerank = mode is None or mode == 'rerank'
		resources = SearchResources(
			embedder=self._load_embedder(self.config) if need_dense and self.config.dense.enabled else None,
			reranker=self._load_reranker(self.config) if need_rerank else None,
			synonyms=load_synonyms(self.root / SYNONYMS_NAME),
		)
		return SearchEngine(self.store, self.config, self.tokenizer, resources)

	@_runtime_errors
	def read(self, doc_id: str, *, section: str | None = None, options: ReadOptions | None = None) -> ReadResult | None:
		"""Read a document or one section. 读取整篇或指定章节。"""
		opts = options or ReadOptions()
		if opts.offset < 0:
			raise ConfigError('offset 必须 ≥ 0')
		if opts.max_chars <= 0:
			raise ConfigError('max_chars 必须是正整数')
		doc_id = unicodedata.normalize('NFC', doc_id)
		if opts.sync:
			self._auto_sync()
		row = self.store.get_doc(doc_id)
		if row is None:
			return None
		path = self.root / row['path']
		if not path.is_file():
			return None
		parsed = parse_file(path, doc_id, index_dir=self.config.index_dir)
		text = parsed.text
		if section is not None:
			found = find_section(parsed, [part.strip() for part in section.split(' > ')])
			if found is None:
				return None
			text = text[found[0] : found[1]].rstrip('\n')
		slice_text = text[opts.offset : opts.offset + opts.max_chars]
		next_offset = opts.offset + len(slice_text)
		return ReadResult(
			doc_id=doc_id,
			doc_title=row['title'],
			scope=row['scope'],
			tags=json.loads(row['tags']),
			text=slice_text,
			offset=opts.offset,
			next_offset=None if next_offset >= len(text) else next_offset,
		)

	@_runtime_errors
	def show(self, chunk_id: str) -> ChunkDetail | None:
		"""Show one chunk. 显示单个段。"""
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

	@_runtime_errors
	def list_docs(
		self,
		*,
		scope: list[str] | None = None,
		options: ListDocsOptions | None = None,
	) -> DocsResult:
		"""List documents with optional filters. 列出文档。"""
		opts = options or ListDocsOptions()
		filters = replace(opts.filters, scope=tuple(scope)) if scope else opts.filters
		if opts.sync:
			self._auto_sync()
		where_sql, params = filters.normalized().where('docs')
		rows = self.store.docs_with_filters(where_sql, params)
		return DocsResult(
			docs=[
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
		)

	@_runtime_errors
	def list_scopes(self, *, sync: bool = True) -> ScopesResult:
		"""List scopes and document counts. 列出专题。"""
		if sync:
			self._auto_sync()
		stats = dict(self.store.scope_stats())
		names = sorted(set(stats) | set(self.config.scopes))
		return ScopesResult(
			scopes=[ScopeInfo(name, self.config.scopes.get(name), stats.get(name, 0)) for name in names]
		)

	@_runtime_errors
	def doctor(self) -> DoctorResult:
		"""Report index and model status. 输出索引与模型状态。"""
		stats = self.store.stats()
		scan_result = scan(self.root, self.config)
		dense, rerank = self._model_status()
		return DoctorResult(
			root=str(self.root),
			index_dir=str(self.config.index_dir),
			config_files=[str(path) for path in self.config.config_files],
			docs=stats['docs'],
			chunks=stats['chunks'],
			scopes=dict(self.store.scope_stats()),
			file_types=self.store.file_type_stats(),
			skipped=[SkippedEntry(item.doc_id, item.reason) for item in scan_result.skipped],
			failed=[FailedEntry(row['doc_id'], row['reason']) for row in self.store.list_failures()],
			dense=dense,
			rerank=rerank,
			index_bytes=_directory_size(self.config.index_dir),
		)

	def _model_status(self) -> tuple[dict[str, Any], dict[str, Any]]:
		dense_dir = model_dir_for(self.config.dense, self.config.models_dir)
		dense_ready = model_ready(dense_dir)
		dense = {
			'enabled': self.config.dense.enabled,
			'model': self.config.dense.model,
			'ready': dense_ready,
			'identity': model_identity(dense_dir) if dense_ready else None,
		}
		rerank_dir = self.config.rerank.model_dir
		rerank_ready_value = rerank_ready(rerank_dir) if rerank_dir else False
		rerank = {
			'enabled': self.config.rerank.enabled,
			'model_dir': str(rerank_dir) if rerank_dir else None,
			'ready': rerank_ready_value,
			'identity': rerank_model_identity(rerank_dir) if rerank_dir and rerank_ready_value else None,
		}
		return dense, rerank

	@_runtime_errors
	def evaluate(self, queries_path: str | Path, modes: list[str], *, k: int = 5) -> EvalResult:
		"""Evaluate an eval set. 执行评测。"""
		from axc_wensuo.evaluate import evaluate as run_evaluate
		from axc_wensuo.evaluate import load_queries

		self._auto_sync()
		queries = load_queries(queries_path, default_k=k)
		return run_evaluate(lambda request: self._engine(request.mode).search(request), queries, modes, k=k)

	@_runtime_errors
	def gaps(self, *, limit: int = 50) -> GapsResult:
		if limit <= 0:
			raise ConfigError('limit 必须是正整数')
		return list_gaps_impl(self.store, limit)

	@_runtime_errors
	def clear_gaps(self) -> int:
		return self.store.clear_gaps()

	def close(self) -> None:
		if not self._closed:
			self.store.close()
			self._closed = True

	def __enter__(self) -> KnowledgeBase:
		return self

	def __exit__(self, *exc_info: object) -> None:
		self.close()

def _normalize_all(values: tuple[str, ...]) -> list[str]:
	return [unicodedata.normalize('NFC', value) for value in values]

def _format_modified(timestamp: float) -> str:
	return dt.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')

def _directory_size(directory: Path) -> int:
	return sum(path.stat().st_size for path in directory.rglob('*') if path.is_file())

def _model_file_signature(model_dir: Path) -> tuple[int, int] | None:
	try:
		stat = (Path(model_dir) / 'model.onnx').stat()
	except OSError:
		return None
	return (stat.st_mtime_ns, stat.st_size)


__all__ = [
	'KnowledgeBase',
	'ListDocsOptions',
	'ReadOptions',
	'SearchOptions',
]
