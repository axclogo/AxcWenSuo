"""File hashing helpers. 文件哈希工具。"""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK_SIZE = 1024 * 1024


def file_sha256(path: str | Path) -> str:
	"""Hash a file in streaming mode. 流式计算文件 SHA-256。"""
	digest = hashlib.sha256()
	with Path(path).open('rb') as handle:
		for chunk in iter(lambda: handle.read(CHUNK_SIZE), b''):
			digest.update(chunk)
	return digest.hexdigest()
