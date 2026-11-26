"""Public dataclasses and JSON structures. 对外数据结构和 JSON 结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Location:
	"""Location in the source. 段在原文中的位置。"""

	kind: str
	start: int | None = None
	end: int | None = None
	sheet: str | None = None
	row_start: int | None = None
	row_end: int | None = None

	def to_dict(self) -> dict[str, Any]:
		if self.kind in ('lines', 'page', 'slide'):
			return {'kind': self.kind, 'start': self.start, 'end': self.end}
		if self.kind == 'sheet':
			return {'kind': 'sheet', 'sheet': self.sheet, 'row_start': self.row_start, 'row_end': self.row_end}
		return {'kind': self.kind}

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> Location:
		return cls(
			kind=str(data['kind']),
			start=data.get('start'),
			end=data.get('end'),
			sheet=data.get('sheet'),
			row_start=data.get('row_start'),
			row_end=data.get('row_end'),
		)

	def union(self, other: Location) -> Location:
		"""Merge two locations. 合并两个位置。"""
		if other is None or other.kind != self.kind:
			return self
		if self.kind in ('lines', 'page', 'slide'):
			return Location(
				kind=self.kind,
				start=min(self.start or 0, other.start or 0),
				end=max(self.end or 0, other.end or 0),
			)
		if self.kind == 'sheet':
			same_sheet = self.sheet == other.sheet
			return Location(
				kind='sheet',
				sheet=self.sheet if same_sheet else (self.sheet or other.sheet),
				row_start=min(self.row_start or 0, other.row_start or 0),
				row_end=max(self.row_end or 0, other.row_end or 0),
			)
		return self

@dataclass(frozen=True)
class ScopeRule:
	"""One scope rule. 一条专题规则。"""

	glob: str
	scope: str

@dataclass
class Document:
	doc_id: str
	doc_title: str
	scope: str
	tags: list[str] = field(default_factory=list)
	chunks: int = 0
	modified: str = ''

	def to_dict(self) -> dict[str, Any]:
		return {
			'doc_id': self.doc_id,
			'doc_title': self.doc_title,
			'scope': self.scope,
			'tags': list(self.tags),
			'chunks': self.chunks,
			'modified': self.modified,
		}

@dataclass
class SearchHit:
	rank: int
	chunk_id: str
	doc_id: str
	doc_title: str
	scope: str
	title_path: list[str]
	location: Location
	score: float
	sources: dict[str, float]
	text: str
	truncated: bool = False
	expanded: bool = False

	def to_dict(self) -> dict[str, Any]:
		return {
			'rank': self.rank,
			'chunk_id': self.chunk_id,
			'doc_id': self.doc_id,
			'doc_title': self.doc_title,
			'scope': self.scope,
			'title_path': list(self.title_path),
			'location': self.location.to_dict(),
			'score': self.score,
			'sources': {key: value for key, value in self.sources.items()},
			'text': self.text,
			'truncated': self.truncated,
			'expanded': self.expanded,
		}

@dataclass
class SearchResult:
	query: str
	stale: bool
	score_kind: str
	identifiers: list[str]
	missing_identifiers: list[str]
	results: list[SearchHit] = field(default_factory=list)

	def to_dict(self) -> dict[str, Any]:
		return {
			'schema_version': SCHEMA_VERSION,
			'query': self.query,
			'stale': self.stale,
			'score_kind': self.score_kind,
			'identifiers': list(self.identifiers),
			'missing_identifiers': list(self.missing_identifiers),
			'results': [hit.to_dict() for hit in self.results],
		}

@dataclass
class ChunkDetail:
	"""Chunk detail returned by show/read. show/read 返回的段信息。"""

	chunk_id: str
	doc_id: str
	doc_title: str
	scope: str
	title_path: list[str]
	location: Location
	ordinal: int
	text: str

	def to_dict(self) -> dict[str, Any]:
		# No query, so no rank; score and sources are empty. 没有查询，因此没有 rank；score 与 sources 为空。
		return {
			'schema_version': SCHEMA_VERSION,
			'chunk_id': self.chunk_id,
			'doc_id': self.doc_id,
			'doc_title': self.doc_title,
			'scope': self.scope,
			'title_path': list(self.title_path),
			'location': self.location.to_dict(),
			'ordinal': self.ordinal,
			'score': None,
			'sources': {},
			'text': self.text,
			'truncated': False,
			'expanded': False,
		}

@dataclass
class ReadResult:
	doc_id: str
	doc_title: str
	scope: str
	tags: list[str]
	text: str
	offset: int
	next_offset: int | None

	def to_dict(self) -> dict[str, Any]:
		return {
			'schema_version': SCHEMA_VERSION,
			'doc_id': self.doc_id,
			'doc_title': self.doc_title,
			'scope': self.scope,
			'tags': list(self.tags),
			'text': self.text,
			'offset': self.offset,
			'next_offset': self.next_offset,
		}

@dataclass
class DocsResult:
	docs: list[Document]

	def to_dict(self) -> dict[str, Any]:
		return {'schema_version': SCHEMA_VERSION, 'docs': [doc.to_dict() for doc in self.docs]}

@dataclass
class ScopeInfo:
	scope: str
	description: str | None
	docs: int

	def to_dict(self) -> dict[str, Any]:
		return {'scope': self.scope, 'description': self.description, 'docs': self.docs}

@dataclass
class ScopesResult:
	scopes: list[ScopeInfo]

	def to_dict(self) -> dict[str, Any]:
		return {'schema_version': SCHEMA_VERSION, 'scopes': [scope.to_dict() for scope in self.scopes]}

@dataclass
class SyncReport:
	added: list[str] = field(default_factory=list)
	modified: list[str] = field(default_factory=list)
	deleted: list[str] = field(default_factory=list)
	skipped: list[str] = field(default_factory=list)
	failed: list[str] = field(default_factory=list)
	chunks_total: int = 0
	seconds: float = 0.0

	def to_dict(self) -> dict[str, Any]:
		return {
			'schema_version': SCHEMA_VERSION,
			'added': list(self.added),
			'modified': list(self.modified),
			'deleted': list(self.deleted),
			'skipped': list(self.skipped),
			'failed': list(self.failed),
			'chunks_total': self.chunks_total,
			'seconds': self.seconds,
		}

@dataclass
class SkippedEntry:
	doc_id: str
	reason: str

	def to_dict(self) -> dict[str, Any]:
		return {'doc_id': self.doc_id, 'reason': self.reason}

@dataclass
class FailedEntry:
	doc_id: str
	reason: str

	def to_dict(self) -> dict[str, Any]:
		return {'doc_id': self.doc_id, 'reason': self.reason}

@dataclass
class DoctorResult:
	root: str
	index_dir: str
	config_files: list[str]
	docs: int
	chunks: int
	scopes: dict[str, int]
	file_types: dict[str, int]
	skipped: list[SkippedEntry]
	failed: list[FailedEntry]
	dense: dict[str, Any]
	rerank: dict[str, Any]
	index_bytes: int

	def to_dict(self) -> dict[str, Any]:
		return {
			'schema_version': SCHEMA_VERSION,
			'root': self.root,
			'index_dir': self.index_dir,
			'config_files': list(self.config_files),
			'docs': self.docs,
			'chunks': self.chunks,
			'scopes': dict(self.scopes),
			'file_types': dict(self.file_types),
			'skipped': [entry.to_dict() for entry in self.skipped],
			'failed': [entry.to_dict() for entry in self.failed],
			'dense': dict(self.dense),
			'rerank': dict(self.rerank),
			'index_bytes': self.index_bytes,
		}

@dataclass
class GapEntry:
	query: str
	filters: dict[str, Any]
	entry: str
	count: int
	first_seen: str
	last_seen: str
	last_result_count: int
	missing_identifiers: list[str]

	def to_dict(self) -> dict[str, Any]:
		return {
			'query': self.query,
			'filters': dict(self.filters),
			'entry': self.entry,
			'count': self.count,
			'first_seen': self.first_seen,
			'last_seen': self.last_seen,
			'last_result_count': self.last_result_count,
			'missing_identifiers': list(self.missing_identifiers),
		}

@dataclass
class GapsResult:
	gaps: list[GapEntry]

	def to_dict(self) -> dict[str, Any]:
		return {'schema_version': SCHEMA_VERSION, 'gaps': [gap.to_dict() for gap in self.gaps]}

@dataclass
class EvalModeMetrics:
	hit_at_k: float | None = None
	top1: float | None = None
	mrr_at_k: float | None = None
	answerable_return_rate: float | None = None
	false_return_rate: float | None = None
	n: int = 0

	def to_dict(self) -> dict[str, Any]:
		return {
			'hit_at_k': self.hit_at_k,
			'top1': self.top1,
			'mrr_at_k': self.mrr_at_k,
			'answerable_return_rate': self.answerable_return_rate,
			'false_return_rate': self.false_return_rate,
			'n': self.n,
		}

@dataclass
class EvalResult:
	k: int
	modes: dict[str, EvalModeMetrics]

	def to_dict(self) -> dict[str, Any]:
		return {
			'schema_version': SCHEMA_VERSION,
			'k': self.k,
			'modes': {name: metrics.to_dict() for name, metrics in self.modes.items()},
		}

@dataclass
class ErrorResult:
	code: int
	message: str

	def to_dict(self) -> dict[str, Any]:
		return {'schema_version': SCHEMA_VERSION, 'error': {'code': self.code, 'message': self.message}}
