"""Folder scanning, include rules, and scopes. 目录扫描、收纳规则与专题。"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from axc_wensuo.config import Config
from axc_wensuo.models import ScopeRule
from axc_wensuo.parsers import DOCS_EXTENSIONS, SUPPORTED_EXTENSIONS, docs_extra_available

IGNORED_NAMES = frozenset({'wensuo.yaml', 'synonyms.yaml'})
IGNORED_DIRS = frozenset({'node_modules', '__pycache__'})
ROOT_SCOPE = '_root'


@dataclass(frozen=True)
class FileEntry:
	doc_id: str
	path: Path
	size: int
	mtime_ns: int

@dataclass(frozen=True)
class SkippedFile:
	doc_id: str
	reason: str

@dataclass
class ScanResult:
	files: list[FileEntry]
	skipped: list[SkippedFile]

	def by_id(self) -> dict[str, FileEntry]:
		return {entry.doc_id: entry for entry in self.files}

def scan(root: str | Path, config: Config) -> ScanResult:
	"""Scan the root and classify files. 扫描根目录并分类文件。"""
	root_path = Path(root).resolve()
	index_dir = config.index_dir.resolve()
	docs_available = docs_extra_available()
	include_patterns = [_glob_regex(pattern) for pattern in config.include]
	exclude_patterns = [_glob_regex(pattern) for pattern in config.exclude]
	max_bytes = config.max_file_mb * 1024 * 1024
	files: list[FileEntry] = []
	skipped: list[SkippedFile] = []

	for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
		current = Path(dirpath)
		dirnames[:] = sorted(
			name
			for name in dirnames
			if not name.startswith('.') and name not in IGNORED_DIRS and (current / name).resolve() != index_dir
		)
		for name in sorted(filenames):
			if name.startswith('.'):
				continue
			extension = Path(name).suffix.lower()
			if extension not in SUPPORTED_EXTENSIONS:
				continue
			path = current / name
			doc_id = unicodedata.normalize('NFC', path.relative_to(root_path).as_posix())
			if name in IGNORED_NAMES:
				continue
			if include_patterns and not any(pattern.match(doc_id) for pattern in include_patterns):
				skipped.append(SkippedFile(doc_id, '不在 include 列表内'))
				continue
			if any(pattern.match(doc_id) for pattern in exclude_patterns):
				skipped.append(SkippedFile(doc_id, '被 exclude 排除'))
				continue
			if extension in DOCS_EXTENSIONS and not docs_available:
				skipped.append(SkippedFile(doc_id, '缺少 docs 扩展'))
				continue
			try:
				stat = path.stat()
			except OSError as exc:
				skipped.append(SkippedFile(doc_id, f'无法读取文件信息：{exc}'))
				continue
			if stat.st_size > max_bytes:
				skipped.append(SkippedFile(doc_id, f'超过 max_file_mb（{config.max_file_mb} MB）'))
				continue
			files.append(FileEntry(doc_id=doc_id, path=path, size=stat.st_size, mtime_ns=stat.st_mtime_ns))
	files.sort(key=lambda entry: entry.doc_id)
	skipped.sort(key=lambda entry: entry.doc_id)
	return ScanResult(files=files, skipped=skipped)

def _glob_regex(pattern: str) -> re.Pattern[str]:
	"""Translate a glob to regex. glob 转正则。"""
	normalized = pattern.replace('\\', '/').lstrip('/')
	parts: list[str] = []
	index = 0
	while index < len(normalized):
		char = normalized[index]
		if char == '*':
			if normalized.startswith('**/', index):
				parts.append('(?:[^/]+/)*')
				index += 3
			elif normalized.startswith('**', index):
				parts.append('.*')
				index += 2
			else:
				parts.append('[^/]*')
				index += 1
		elif char == '?':
			parts.append('[^/]')
			index += 1
		elif char == '[':
			close = normalized.find(']', index + 1)
			if close == -1:
				parts.append(r'\[')
				index += 1
			else:
				parts.append(normalized[index : close + 1])
				index = close + 1
		else:
			parts.append(re.escape(char))
			index += 1
	return re.compile('^' + ''.join(parts) + '$')

def stat_changed(entry: FileEntry, doc: dict) -> bool:
	"""Cheap size/mtime check first. 先比较大小与修改时间。"""
	return entry.size != doc['size'] or entry.mtime_ns != doc['mtime_ns']

def resolve_scope(doc_id: str, meta_scope: str | None, rules: tuple[ScopeRule, ...]) -> str:
	"""Resolve scope by FR-121 priority. 按 FR-121 优先级确定专题。"""
	doc_id = unicodedata.normalize('NFC', doc_id)
	if meta_scope:
		return unicodedata.normalize('NFC', meta_scope.strip())
	for rule in rules:
		if _glob_regex(rule.glob).match(doc_id):
			return unicodedata.normalize('NFC', rule.scope)
	first = doc_id.split('/', 1)[0]
	if '/' in doc_id and first:
		return unicodedata.normalize('NFC', first)
	return ROOT_SCOPE
