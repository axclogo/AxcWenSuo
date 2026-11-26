"""SQLite 索引存储（FR-1201）与索引目录文件锁（FR-207）。

索引目录结构::

	<index_dir>/
	  index.sqlite   # WAL 模式，文档、段、倒排、向量、缺口、失败记录
	  parsed/        # Docling 解析缓存（由 parsers.docling 写入）
	  lock           # filelock 文件锁

读写约定：写入方先拿 ``lock``；每个文件（文档）的增删改在一个事务内完成，读者只看到已提交数据。
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import filelock

from axc_wensuo.errors import WenSuoRuntimeError
from axc_wensuo.models import Location
from axc_wensuo.tokenizers import identifier_hit

SCHEMA_VERSION = 2
DB_NAME = 'index.sqlite'
LOCK_NAME = 'lock'
PARSED_DIR = 'parsed'

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
	key TEXT PRIMARY KEY,
	value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS docs (
	doc_id TEXT PRIMARY KEY,
	title TEXT NOT NULL,
	scope TEXT NOT NULL,
	tags TEXT NOT NULL,
	aliases TEXT NOT NULL,
	size INTEGER NOT NULL,
	mtime_ns INTEGER NOT NULL,
	sha256 TEXT NOT NULL,
	parser TEXT NOT NULL,
	modified_ts REAL NOT NULL,
	chunks INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chunks (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	chunk_id TEXT NOT NULL UNIQUE,
	doc_id TEXT NOT NULL,
	ordinal INTEGER NOT NULL,
	scope TEXT NOT NULL,
	title_path TEXT NOT NULL,
	text TEXT NOT NULL,
	index_text TEXT NOT NULL,
	location TEXT NOT NULL,
	dl REAL NOT NULL,
	vec BLOB,
	modified_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS terms (
	id INTEGER PRIMARY KEY,
	term TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS postings (
	term_id INTEGER NOT NULL,
	chunk_pk INTEGER NOT NULL,
	tf_body INTEGER NOT NULL,
	tf_meta INTEGER NOT NULL,
	PRIMARY KEY (term_id, chunk_pk)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_postings_chunk ON postings(chunk_pk);
CREATE TABLE IF NOT EXISTS vector_cache (
	text_sha TEXT NOT NULL,
	identity TEXT NOT NULL,
	vec BLOB NOT NULL,
	PRIMARY KEY (text_sha, identity)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS doc_tags (
	doc_id TEXT NOT NULL,
	tag TEXT NOT NULL,
	PRIMARY KEY (doc_id, tag)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ident_run (
	run TEXT PRIMARY KEY,
	n INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS chunk_ident (
	chunk_pk INTEGER NOT NULL,
	run TEXT NOT NULL,
	PRIMARY KEY (chunk_pk, run)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS failures (
	doc_id TEXT PRIMARY KEY,
	reason TEXT NOT NULL,
	sha256 TEXT NOT NULL,
	parser_version TEXT NOT NULL,
	size INTEGER NOT NULL,
	mtime_ns INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS gaps (
	query TEXT NOT NULL,
	filters TEXT NOT NULL,
	entry TEXT NOT NULL,
	count INTEGER NOT NULL,
	first_seen TEXT NOT NULL,
	last_seen TEXT NOT NULL,
	last_result_count INTEGER NOT NULL,
	missing_identifiers TEXT NOT NULL,
	PRIMARY KEY (query, filters, entry)
) WITHOUT ROWID;
"""


def _dumps(value: Any) -> str:
	return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _loads(value: str | None, default: Any = None) -> Any:
	if not value:
		return default
	return json.loads(value)


class IndexLock:
	"""索引目录写锁。``acquire`` 返回是否拿到锁，不抛 filelock 异常。"""

	def __init__(self, index_dir: Path) -> None:
		self.path = Path(index_dir) / LOCK_NAME
		self._lock = filelock.FileLock(str(self.path))

	def acquire(self, timeout: float) -> bool:
		try:
			self._lock.acquire(timeout=timeout)
		except filelock.Timeout:
			return False
		return True

	def release(self) -> None:
		if self._lock.is_locked:
			self._lock.release()

	def __enter__(self) -> IndexLock:
		return self

	def __exit__(self, *exc_info: object) -> None:
		self.release()


class Store:
	"""一个知识库的 SQLite 索引。线程安全不做要求：一个线程一个实例。"""

	def __init__(self, index_dir: Path) -> None:
		self.index_dir = Path(index_dir)
		self.index_dir.mkdir(parents=True, exist_ok=True)
		self.path = self.index_dir / DB_NAME
		self.conn = sqlite3.connect(str(self.path), timeout=30.0, isolation_level=None)
		self.conn.row_factory = sqlite3.Row
		self.conn.execute('PRAGMA busy_timeout=10000')
		self.conn.execute('PRAGMA synchronous=NORMAL')
		self.conn.execute('PRAGMA temp_store=MEMORY')
		self.conn.execute('PRAGMA cache_size=-32000')
		self._retry_locked(lambda: self.conn.execute('PRAGMA journal_mode=WAL'))
		self._init_schema()

	def _init_schema(self) -> None:
		version = self._schema_version()
		if version == SCHEMA_VERSION:
			return
		if version is not None and version != SCHEMA_VERSION:
			raise WenSuoRuntimeError(
				f'索引版本 {version} 与当前程序要求的 {SCHEMA_VERSION} 不兼容；'
				f'请删除索引目录 {self.index_dir} 后重新同步'
			)
		self._retry_locked(lambda: self.conn.executescript(SCHEMA_SQL))
		self._retry_locked(
			lambda: self.conn.execute(
				"INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
			)
		)

	def _schema_version(self) -> int | None:
		try:
			row = self.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
		except sqlite3.OperationalError:
			return None
		return int(row['value']) if row is not None else None

	@staticmethod
	def _retry_locked(action: Callable[[], Any], *, timeout: float = 30.0) -> None:
		"""数据库被另一个进程短暂占用时重试（FR-207：写锁由文件锁串行化，这里只处理初始化竞争）。"""
		deadline = time.monotonic() + timeout
		while True:
			try:
				action()
				return
			except sqlite3.OperationalError as exc:
				if 'locked' not in str(exc).lower() and 'busy' not in str(exc).lower():
					raise
				if time.monotonic() >= deadline:
					raise
				time.sleep(0.05)

	def close(self) -> None:
		self.conn.close()

	def __enter__(self) -> Store:
		return self

	def __exit__(self, *exc_info: object) -> None:
		self.close()

	@contextmanager
	def transaction(self) -> Iterator[sqlite3.Connection]:
		"""一个写事务；异常时回滚。"""
		self.conn.execute('BEGIN IMMEDIATE')
		try:
			yield self.conn
		except BaseException:
			self.conn.execute('ROLLBACK')
			raise
		else:
			self.conn.execute('COMMIT')

	# ---------------------------------------------------------------- meta

	def get_meta(self, key: str, default: str | None = None) -> str | None:
		row = self.conn.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
		return row['value'] if row is not None else default

	def set_meta(self, key: str, value: str) -> None:
		self.conn.execute(
			'INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
			(key, value),
		)

	def set_meta_many(self, values: dict[str, str]) -> None:
		self.conn.executemany(
			'INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
			list(values.items()),
		)

	def has_index(self) -> bool:
		"""索引是否已经至少完整建立过一次。"""
		return self.get_meta('built_at') is not None

	def chunk_signature(self) -> str | None:
		return self.get_meta('chunk_sig')

	def dense_signature(self) -> str | None:
		return self.get_meta('dense_sig')

	# ---------------------------------------------------------------- docs

	def get_doc(self, doc_id: str) -> dict[str, Any] | None:
		row = self.conn.execute('SELECT * FROM docs WHERE doc_id = ?', (doc_id,)).fetchone()
		return dict(row) if row is not None else None

	def all_docs(self) -> dict[str, dict[str, Any]]:
		rows = self.conn.execute('SELECT * FROM docs').fetchall()
		return {row['doc_id']: dict(row) for row in rows}

	def count_docs(self) -> int:
		return int(self.conn.execute('SELECT COUNT(*) FROM docs').fetchone()[0])

	def count_chunks(self) -> int:
		return int(self.conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0])

	def upsert_doc(self, doc: dict[str, Any]) -> None:
		"""写入文档行与标签；调用方负责事务。"""
		self.conn.execute(
			"""
			INSERT INTO docs(doc_id, title, scope, tags, aliases, size, mtime_ns, sha256, parser, modified_ts, chunks)
			VALUES (:doc_id, :title, :scope, :tags, :aliases, :size, :mtime_ns, :sha256, :parser, :modified_ts, :chunks)
			ON CONFLICT(doc_id) DO UPDATE SET
				title = excluded.title, scope = excluded.scope, tags = excluded.tags, aliases = excluded.aliases,
				size = excluded.size, mtime_ns = excluded.mtime_ns, sha256 = excluded.sha256,
				parser = excluded.parser, modified_ts = excluded.modified_ts, chunks = excluded.chunks
			""",
			doc,
		)
		self.conn.execute('DELETE FROM doc_tags WHERE doc_id = ?', (doc['doc_id'],))
		self.conn.executemany(
			'INSERT OR IGNORE INTO doc_tags(doc_id, tag) VALUES (?, ?)',
			[(doc['doc_id'], tag) for tag in _loads(doc['tags'], [])],
		)

	def delete_document(self, doc_id: str) -> None:
		"""删除文档及其段、倒排、向量、标签、失败记录；调用方负责事务。"""
		chunk_pks = [row[0] for row in self.conn.execute('SELECT id FROM chunks WHERE doc_id = ?', (doc_id,))]
		if chunk_pks:
			self._delete_chunks(chunk_pks)
		self.conn.execute('DELETE FROM doc_tags WHERE doc_id = ?', (doc_id,))
		self.conn.execute('DELETE FROM docs WHERE doc_id = ?', (doc_id,))
		self.conn.execute('DELETE FROM failures WHERE doc_id = ?', (doc_id,))

	def _delete_chunks(self, chunk_pks: Iterable[int]) -> None:
		pks = list(chunk_pks)
		if not pks:
			return
		for start in range(0, len(pks), 500):
			batch = pks[start : start + 500]
			placeholders = ', '.join('?' for _ in batch)
			rows = self.conn.execute(
				f'SELECT run, COUNT(*) AS n FROM chunk_ident WHERE chunk_pk IN ({placeholders}) GROUP BY run',
				batch,
			).fetchall()
			for row in rows:
				self.conn.execute('UPDATE ident_run SET n = n - ? WHERE run = ?', (row['n'], row['run']))
			self.conn.execute(f'DELETE FROM chunk_ident WHERE chunk_pk IN ({placeholders})', batch)
			self.conn.execute(f'DELETE FROM postings WHERE chunk_pk IN ({placeholders})', batch)
			self.conn.execute(f'DELETE FROM chunks WHERE id IN ({placeholders})', batch)
		self.conn.execute('DELETE FROM ident_run WHERE n <= 0')

	# ---------------------------------------------------------------- chunks

	def insert_chunk(self, chunk: dict[str, Any]) -> int:
		"""插入一个段并返回主键；``chunk['location']`` 与 ``title_path`` 为 JSON 可序列化值。"""
		cursor = self.conn.execute(
			"""
			INSERT INTO chunks(
				chunk_id, doc_id, ordinal, scope, title_path, text, index_text, location, dl, vec, modified_ts
			)
			VALUES (
				:chunk_id, :doc_id, :ordinal, :scope, :title_path, :text, :index_text, :location, :dl, :vec,
				:modified_ts
			)
			""",
			{
				'chunk_id': chunk['chunk_id'],
				'doc_id': chunk['doc_id'],
				'ordinal': chunk['ordinal'],
				'scope': chunk['scope'],
				'title_path': _dumps(chunk['title_path']),
				'text': chunk['text'],
				'index_text': chunk['index_text'],
				'location': _dumps(chunk['location']),
				'dl': chunk['dl'],
				'vec': chunk.get('vec'),
				'modified_ts': chunk['modified_ts'],
			},
		)
		return int(cursor.lastrowid)

	def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
		row = self.conn.execute(
			"""
			SELECT c.chunk_id, c.doc_id, d.title AS doc_title, c.scope, c.title_path, c.location, c.ordinal,
			       c.text, c.modified_ts
			FROM chunks AS c JOIN docs AS d ON d.doc_id = c.doc_id
			WHERE c.chunk_id = ?
			""",
			(chunk_id,),
		).fetchone()
		if row is None:
			return None
		data = dict(row)
		data['title_path'] = _loads(data['title_path'], [])
		data['location'] = Location.from_dict(_loads(data['location'], {'kind': 'section'}))
		return data

	def chunks_for_doc(self, doc_id: str) -> list[dict[str, Any]]:
		rows = self.conn.execute('SELECT * FROM chunks WHERE doc_id = ? ORDER BY ordinal', (doc_id,)).fetchall()
		return [self._chunk_row(row) for row in rows]

	@staticmethod
	def _chunk_row(row: sqlite3.Row) -> dict[str, Any]:
		data = dict(row)
		data['title_path'] = _loads(data['title_path'], [])
		data['location'] = Location.from_dict(_loads(data['location'], {'kind': 'section'}))
		return data

	def docs_with_filters(self, where_sql: str, params: list[Any]) -> list[dict[str, Any]]:
		clause = f'WHERE {where_sql}' if where_sql else ''
		rows = self.conn.execute(
			f'SELECT doc_id, title, scope, tags, chunks, modified_ts FROM docs {clause} ORDER BY doc_id',
			params,
		).fetchall()
		documents: list[dict[str, Any]] = []
		for row in rows:
			data = dict(row)
			data['tags'] = _loads(data['tags'], [])
			documents.append(data)
		return documents

	def scope_stats(self) -> list[tuple[str, int]]:
		rows = self.conn.execute('SELECT scope, COUNT(*) AS n FROM docs GROUP BY scope ORDER BY scope').fetchall()
		return [(row['scope'], int(row['n'])) for row in rows]

	def file_type_stats(self) -> dict[str, int]:
		stats: dict[str, int] = {}
		for row in self.conn.execute('SELECT doc_id FROM docs').fetchall():
			suffix = Path(row['doc_id']).suffix.lower().lstrip('.')
			if suffix:
				stats[suffix] = stats.get(suffix, 0) + 1
		return dict(sorted(stats.items()))

	def cached_vectors(self, identity: str, text_hashes: Iterable[str]) -> dict[str, bytes]:
		"""按「段索引文本 SHA-256 + 模型身份」复用向量（FR-205）。"""
		hashes = list(dict.fromkeys(text_hashes))
		result: dict[str, bytes] = {}
		for start in range(0, len(hashes), 500):
			batch = hashes[start : start + 500]
			placeholders = ', '.join('?' for _ in batch)
			rows = self.conn.execute(
				f'SELECT text_sha, vec FROM vector_cache WHERE identity = ? AND text_sha IN ({placeholders})',
				[identity, *batch],
			)
			for row in rows:
				result[row['text_sha']] = row['vec']
		return result

	def store_vectors(self, identity: str, rows: list[tuple[str, bytes]]) -> None:
		self.conn.executemany(
			'INSERT OR REPLACE INTO vector_cache(text_sha, identity, vec) VALUES (?, ?, ?)',
			[(text_sha, identity, vec) for text_sha, vec in rows],
		)

	def prune_vector_cache(self, identity: str) -> None:
		self.conn.execute('DELETE FROM vector_cache WHERE identity != ?', (identity,))

	def update_vectors(self, rows: list[tuple[bytes, int]]) -> None:
		self.conn.executemany('UPDATE chunks SET vec = ? WHERE id = ?', rows)

	def clear_vectors(self) -> None:
		self.conn.execute('UPDATE chunks SET vec = NULL')

	# ---------------------------------------------------------------- terms & postings

	def load_terms(self) -> dict[str, int]:
		return {row['term']: int(row['id']) for row in self.conn.execute('SELECT id, term FROM terms').fetchall()}

	def insert_terms(self, items: list[tuple[int, str]]) -> None:
		self.conn.executemany('INSERT OR IGNORE INTO terms(id, term) VALUES (?, ?)', items)

	def max_term_id(self) -> int:
		row = self.conn.execute('SELECT MAX(id) FROM terms').fetchone()
		return int(row[0]) if row[0] is not None else 0

	def term_ids(self, tokens: Iterable[str]) -> dict[str, int]:
		values = list(dict.fromkeys(tokens))
		result: dict[str, int] = {}
		for start in range(0, len(values), 500):
			batch = values[start : start + 500]
			placeholders = ', '.join('?' for _ in batch)
			for row in self.conn.execute(f'SELECT id, term FROM terms WHERE term IN ({placeholders})', batch):
				result[row['term']] = int(row['id'])
		return result

	def insert_postings(self, rows: list[tuple[int, int, int, int]]) -> None:
		"""rows: (term_id, chunk_pk, tf_body, tf_meta)。"""
		self.conn.executemany(
			'INSERT OR REPLACE INTO postings(term_id, chunk_pk, tf_body, tf_meta) VALUES (?, ?, ?, ?)', rows
		)

	def term_df(self, term_id: int) -> int:
		return int(self.conn.execute('SELECT COUNT(*) FROM postings WHERE term_id = ?', (term_id,)).fetchone()[0])

	def postings_with_filters(self, term_id: int, where_sql: str, params: list[Any]) -> list[sqlite3.Row]:
		clause = f'AND {where_sql}' if where_sql else ''
		return self.conn.execute(
			f"""
			SELECT p.chunk_pk, p.tf_body, p.tf_meta, c.dl, c.chunk_id
			FROM postings AS p JOIN chunks AS c ON c.id = p.chunk_pk
			WHERE p.term_id = ? {clause}
			""",
			[term_id, *params],
		).fetchall()

	def vectors_with_filters(self, where_sql: str, params: list[Any]) -> list[sqlite3.Row]:
		clause = f'WHERE vec IS NOT NULL AND {where_sql}' if where_sql else 'WHERE vec IS NOT NULL'
		return self.conn.execute(
			f'SELECT id, chunk_id, vec FROM chunks {clause}',
			params,
		).fetchall()

	# ---------------------------------------------------------------- identifier runs

	def add_ident_runs(self, chunk_pk: int, runs: Iterable[str]) -> None:
		values = list(dict.fromkeys(runs))
		self.conn.executemany(
			'INSERT OR IGNORE INTO chunk_ident(chunk_pk, run) VALUES (?, ?)',
			[(chunk_pk, run) for run in values],
		)
		self.conn.executemany(
			'INSERT INTO ident_run(run, n) VALUES (?, 1) ON CONFLICT(run) DO UPDATE SET n = n + 1',
			[(run,) for run in values],
		)

	def ident_runs_for_chunks(self, chunk_pks: Iterable[int]) -> dict[int, list[str]]:
		pks = list(chunk_pks)
		result: dict[int, list[str]] = {pk: [] for pk in pks}
		for start in range(0, len(pks), 500):
			batch = pks[start : start + 500]
			placeholders = ', '.join('?' for _ in batch)
			for row in self.conn.execute(
				f'SELECT chunk_pk, run FROM chunk_ident WHERE chunk_pk IN ({placeholders})', batch
			):
				result[int(row['chunk_pk'])].append(row['run'])
		return result

	def ident_runs_matching(self, pattern: str) -> list[str]:
		return [
			row['run']
			for row in self.conn.execute("SELECT run FROM ident_run WHERE run LIKE ? ESCAPE '\\'", (pattern,))
		]

	# ---------------------------------------------------------------- failures

	def get_failure(self, doc_id: str) -> dict[str, Any] | None:
		row = self.conn.execute('SELECT * FROM failures WHERE doc_id = ?', (doc_id,)).fetchone()
		return dict(row) if row is not None else None

	def set_failure(self, doc_id: str, reason: str, sha256: str, parser_version: str, size: int, mtime_ns: int) -> None:
		self.conn.execute(
			"""
			INSERT INTO failures(doc_id, reason, sha256, parser_version, size, mtime_ns)
			VALUES (?, ?, ?, ?, ?, ?)
			ON CONFLICT(doc_id) DO UPDATE SET
				reason = excluded.reason, sha256 = excluded.sha256, parser_version = excluded.parser_version,
				size = excluded.size, mtime_ns = excluded.mtime_ns
			""",
			(doc_id, reason, sha256, parser_version, size, mtime_ns),
		)

	def delete_failure(self, doc_id: str) -> None:
		self.conn.execute('DELETE FROM failures WHERE doc_id = ?', (doc_id,))

	def list_failures(self) -> list[dict[str, Any]]:
		rows = self.conn.execute(
			'SELECT doc_id, reason, sha256, parser_version, size, mtime_ns FROM failures ORDER BY doc_id'
		)
		return [dict(row) for row in rows]

	# ---------------------------------------------------------------- gaps

	def record_gap(
		self,
		*,
		query: str,
		filters_json: str,
		entry: str,
		last_result_count: int,
		missing_identifiers: list[str],
		now: str,
	) -> None:
		self.conn.execute(
			"""
			INSERT INTO gaps(
				query, filters, entry, count, first_seen, last_seen, last_result_count, missing_identifiers
			)
			VALUES (?, ?, ?, 1, ?, ?, ?, ?)
			ON CONFLICT(query, filters, entry) DO UPDATE SET
				count = count + 1,
				last_seen = excluded.last_seen,
				last_result_count = excluded.last_result_count,
				missing_identifiers = excluded.missing_identifiers
			""",
			(query, filters_json, entry, now, now, last_result_count, _dumps(missing_identifiers)),
		)

	def list_gaps(self, limit: int) -> list[dict[str, Any]]:
		rows = self.conn.execute('SELECT * FROM gaps ORDER BY count DESC, last_seen DESC LIMIT ?', (limit,)).fetchall()
		return [dict(row) for row in rows]

	def clear_gaps(self) -> int:
		return int(self.conn.execute('DELETE FROM gaps').rowcount)

	def stats(self) -> dict[str, int]:
		return {
			'docs': self.count_docs(),
			'chunks': self.count_chunks(),
		}

	def avg_dl(self) -> float:
		row = self.conn.execute('SELECT AVG(dl) FROM chunks').fetchone()
		return float(row[0]) if row[0] is not None else 0.0

	def chunks_by_pks(self, chunk_pks: Iterable[int]) -> dict[int, dict[str, Any]]:
		"""按主键批量取段的检索所需字段。"""
		pks = list(chunk_pks)
		result: dict[int, dict[str, Any]] = {}
		for start in range(0, len(pks), 500):
			batch = pks[start : start + 500]
			placeholders = ', '.join('?' for _ in batch)
			rows = self.conn.execute(
				f"""
				SELECT c.id, c.chunk_id, c.doc_id, d.title AS doc_title, c.scope, c.title_path, c.location,
				       c.ordinal, c.text, c.index_text, c.modified_ts
				FROM chunks AS c JOIN docs AS d ON d.doc_id = c.doc_id
				WHERE c.id IN ({placeholders})
				""",
				batch,
			).fetchall()
			for row in rows:
				data = self._chunk_row(row)
				result[int(data['id'])] = data
		return result

	def ident_run_exists(self, identifier: str) -> bool:
		rows = self.conn.execute('SELECT run FROM ident_run WHERE instr(run, ?) > 0', (identifier,))
		return any(identifier_hit(run, identifier) for (run,) in rows)

	def update_doc_stat(self, doc_id: str, size: int, mtime_ns: int, modified_ts: float) -> None:
		self.conn.execute(
			'UPDATE docs SET size = ?, mtime_ns = ?, modified_ts = ? WHERE doc_id = ?',
			(size, mtime_ns, modified_ts, doc_id),
		)
		self.conn.execute(
			'UPDATE chunks SET modified_ts = ? WHERE doc_id = ?',
			(modified_ts, doc_id),
		)

	def count_missing_vectors(self) -> int:
		return int(self.conn.execute('SELECT COUNT(*) FROM chunks WHERE vec IS NULL').fetchone()[0])
