"""ONNX embedder and dense recall. ONNX 向量模型与向量召回。"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from axc_wensuo import embedder_meta
from axc_wensuo.config import DenseConfig
from axc_wensuo.errors import WenSuoRuntimeError
from axc_wensuo.filters import Filters
from axc_wensuo.hashing import file_sha256
from axc_wensuo.store import Store

QUERY_PREFIX = '为这个句子生成表示以用于检索相关文章：'
MODEL_FILENAMES = ('model.onnx', 'tokenizer.json')
MAX_CONTENT_TOKENS = 510
WINDOW_TOKENS = 448
WINDOW_STEP = 384
BATCH_SIZE = 16


def model_dir_for(config: DenseConfig, models_dir: Path) -> Path:
	return Path(models_dir) / config.model

def model_ready(model_dir: str | Path) -> bool:
	directory = Path(model_dir)
	return all((directory / name).is_file() for name in MODEL_FILENAMES)

def require_model(model_dir: str | Path) -> Path:
	directory = Path(model_dir)
	missing = [name for name in MODEL_FILENAMES if not (directory / name).is_file()]
	if missing:
		raise WenSuoRuntimeError(
			f'向量模型未就绪：{directory} 缺少 {", ".join(missing)}。'
			f'请执行 ws models download，或在 wensuo.yaml 中把 models_dir 指向已有模型目录'
		)
	return directory

def model_identity(model_dir: str | Path) -> str:
	"""Identity from model name and file hashes. 模型身份由名称与文件哈希组成。"""
	directory = Path(model_dir)
	return ':'.join([embedder_meta.MODEL_NAME, *(file_sha256(directory / name) for name in MODEL_FILENAMES)])

class Embedder:
	"""BGE ONNX encoder loaded once. BGE ONNX 编码器，只加载一次。"""

	def __init__(self, model_dir: str | Path, *, threads: int | None = None) -> None:
		directory = require_model(model_dir)
		import onnxruntime as ort
		from tokenizers import Tokenizer as HFTokenizer

		self.model_dir = directory
		options = ort.SessionOptions()
		options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
		if threads is not None and threads > 0:
			options.intra_op_num_threads = threads
		options.inter_op_num_threads = 1
		self.session = ort.InferenceSession(
			str(directory / 'model.onnx'), sess_options=options, providers=['CPUExecutionProvider']
		)
		self.tokenizer = HFTokenizer.from_file(str(directory / 'tokenizer.json'))
		self.input_names = {item.name for item in self.session.get_inputs()}
		outputs = self.session.get_outputs()
		if not outputs:
			raise WenSuoRuntimeError(f'向量模型没有输出：{directory / "model.onnx"}')
		self.output_name = next((item.name for item in outputs if item.name == 'embedding'), outputs[0].name)
		self.cls_id, self.sep_id = self._special_ids()
		self.identity = model_identity(directory)
		self.dimension = self._run_ids([[self.cls_id, self.sep_id]]).shape[1]

	def _special_ids(self) -> tuple[int, int]:
		cls = self.tokenizer.token_to_id('[CLS]')
		sep = self.tokenizer.token_to_id('[SEP]')
		if cls is None or sep is None:
			ids = self.tokenizer.encode('', add_special_tokens=True).ids
			if len(ids) < 2:
				raise WenSuoRuntimeError('tokenizer.json 缺少 [CLS] / [SEP] 记号')
			cls, sep = ids[0], ids[-1]
		return int(cls), int(sep)

	def _content_ids(self, text: str) -> list[int]:
		return list(self.tokenizer.encode(text, add_special_tokens=False).ids)

	def _run_ids(self, sequences: list[list[int]]) -> np.ndarray:
		if not sequences:
			return np.zeros((0, 0), dtype=np.float32)
		max_length = max(len(sequence) for sequence in sequences)
		input_ids = np.zeros((len(sequences), max_length), dtype=np.int64)
		attention = np.zeros((len(sequences), max_length), dtype=np.int64)
		for row, sequence in enumerate(sequences):
			input_ids[row, : len(sequence)] = sequence
			attention[row, : len(sequence)] = 1
		feeds: dict[str, np.ndarray] = {'input_ids': input_ids, 'attention_mask': attention}
		if 'token_type_ids' in self.input_names:
			feeds['token_type_ids'] = np.zeros_like(input_ids)
		outputs = self.session.run([self.output_name], feeds)[0]
		return np.asarray(outputs, dtype=np.float32)

	def _encode_window(self, ids: list[int]) -> list[int]:
		return [self.cls_id, *ids, self.sep_id]

	def encode_documents(self, texts: list[str]) -> np.ndarray:
		windows: list[list[int]] = []
		owners: list[int] = []
		for index, text in enumerate(texts):
			content = self._content_ids(text)
			if len(content) <= MAX_CONTENT_TOKENS:
				windows.append(self._encode_window(content))
				owners.append(index)
				continue
			for start in _window_starts(len(content)):
				windows.append(self._encode_window(content[start : start + WINDOW_TOKENS]))
				owners.append(index)
		if not windows:
			return np.zeros((len(texts), self.dimension), dtype=np.float32)
		vectors = np.zeros((len(windows), self.dimension), dtype=np.float32)
		for start in range(0, len(windows), BATCH_SIZE):
			batch = windows[start : start + BATCH_SIZE]
			vectors[start : start + len(batch)] = self._run_ids(batch)
		result = np.zeros((len(texts), self.dimension), dtype=np.float32)
		counts = np.zeros(len(texts), dtype=np.int32)
		for row, owner in enumerate(owners):
			result[owner] += vectors[row]
			counts[owner] += 1
		counts[counts == 0] = 1
		result /= counts[:, None]
		return _l2_normalize(result)

	def encode_query(self, text: str) -> np.ndarray:
		content = self._content_ids(QUERY_PREFIX + text)[:MAX_CONTENT_TOKENS]
		vectors = self._run_ids([self._encode_window(content)])
		return _l2_normalize(vectors)[0]

def _window_starts(length: int) -> list[int]:
	"""Window starts; stop once a window reaches the end. 窗口起点；某窗到达末尾即停止，避免末窗落在前窗之内。"""
	starts = [0]
	while starts[-1] + WINDOW_TOKENS < length:
		starts.append(starts[-1] + WINDOW_STEP)
	return starts

def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
	norm = np.linalg.norm(vectors, axis=1, keepdims=True)
	norm[norm == 0] = 1.0
	return vectors / norm

def vector_to_blob(vector: np.ndarray) -> bytes:
	return np.asarray(vector, dtype=np.float16).tobytes()

@dataclass(frozen=True)
class DenseHit:
	chunk_pk: int
	chunk_id: str
	score: float

class DenseIndex:
	def __init__(self, store: Store) -> None:
		self.store = store

	def search(self, query_vector: np.ndarray, filters: Filters, limit: int) -> list[DenseHit]:
		where_sql, params = filters.where('c')
		rows = self.store.vectors_with_filters(where_sql, params)
		if not rows:
			return []
		chunk_ids: list[str] = []
		chunk_pks: list[int] = []
		blobs: list[np.ndarray] = []
		for row in rows:
			if row['vec'] is None:
				continue
			chunk_pks.append(int(row['id']))
			chunk_ids.append(row['chunk_id'])
			blobs.append(np.frombuffer(row['vec'], dtype=np.float16).astype(np.float32))
		if not blobs:
			return []
		matrix = np.vstack(blobs)
		query = np.asarray(query_vector, dtype=np.float32)
		scores = matrix @ query
		if len(scores) > limit:
			indices = np.argpartition(-scores, limit - 1)[:limit]
			cutoff = float(scores[indices].min())
			indices = np.flatnonzero(scores >= cutoff)
		else:
			indices = np.arange(len(scores))
		order = sorted(
			((float(scores[index]), chunk_ids[index], chunk_pks[index]) for index in indices),
			key=lambda item: (-item[0], item[1]),
		)
		return [DenseHit(chunk_pk=pk, chunk_id=chunk_id, score=score) for score, chunk_id, pk in order[:limit]]

def download_model(models_dir: Path, *, force: bool = False) -> list[Path]:
	"""Download and verify the default model. 下载并校验默认模型。"""
	if not embedder_meta.release_published():
		raise WenSuoRuntimeError(
			f'默认向量模型发布包（{embedder_meta.RELEASE_TAG}）尚未发布；'
			'请运行 tools/export_embedder.py 导出模型，并在 wensuo.yaml 中用 models_dir 指向该目录'
		)
	target = Path(models_dir) / embedder_meta.MODEL_NAME
	target.mkdir(parents=True, exist_ok=True)
	written: list[Path] = []
	for name, expected in embedder_meta.MODEL_FILES.items():
		destination = target / name
		if destination.is_file() and not force and file_sha256(destination) == expected:
			written.append(destination)
			continue
		temporary = destination.with_suffix(destination.suffix + '.part')
		try:
			with urllib.request.urlopen(embedder_meta.download_url(name), timeout=60) as response:
				with temporary.open('wb') as handle:
					while True:
						chunk = response.read(1024 * 1024)
						if not chunk:
							break
						handle.write(chunk)
		except (urllib.error.URLError, OSError) as exc:
			temporary.unlink(missing_ok=True)
			raise WenSuoRuntimeError(f'下载 {name} 失败：{exc}') from exc
		actual = file_sha256(temporary)
		if actual != expected:
			temporary.unlink(missing_ok=True)
			raise WenSuoRuntimeError(f'{name} 的 SHA-256 校验失败：期望 {expected}，实际 {actual}')
		os.replace(temporary, destination)
		written.append(destination)
	return written
__all__: list[Any] = [
	'DenseHit',
	'DenseIndex',
	'Embedder',
	'download_model',
	'model_dir_for',
	'model_identity',
	'model_ready',
	'vector_to_blob',
]
