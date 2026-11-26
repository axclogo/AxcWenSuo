"""Optional ONNX reranker. 可选 ONNX 重排器。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from axc_wensuo.errors import WenSuoRuntimeError
from axc_wensuo.hashing import file_sha256

REQUIRED_META_FIELDS = ('max_length', 'temperature', 'bias', 'default_min_score')


@dataclass(frozen=True)
class RerankMeta:
	max_length: int
	query_max_tokens: int
	passage_chars: int
	temperature: float
	bias: float
	default_min_score: float

def load_rerank_meta(model_dir: str | Path) -> RerankMeta:
	directory = Path(model_dir)
	path = directory / 'model_meta.json'
	if not path.is_file():
		raise WenSuoRuntimeError(f'重排模型目录缺少 model_meta.json：{directory}')
	try:
		data = json.loads(path.read_text(encoding='utf-8'))
	except (OSError, ValueError) as exc:
		raise WenSuoRuntimeError(f'重排模型 model_meta.json 无效：{exc}') from exc
	if not isinstance(data, dict):
		raise WenSuoRuntimeError('重排模型 model_meta.json 必须是 JSON 对象')
	missing = [field for field in REQUIRED_META_FIELDS if field not in data]
	if missing:
		raise WenSuoRuntimeError(f'重排模型 model_meta.json 缺少字段：{", ".join(missing)}')
	max_length = _positive_int(data['max_length'], 'max_length')
	query_max_tokens = _positive_int(data.get('query_max_tokens', 64), 'query_max_tokens')
	passage_chars = _positive_int(data.get('passage_chars', 320), 'passage_chars')
	temperature = _number(data['temperature'], 'temperature')
	bias = _number(data['bias'], 'bias')
	default_min_score = _number(data['default_min_score'], 'default_min_score')
	if temperature == 0:
		raise WenSuoRuntimeError('重排模型 model_meta.json 的 temperature 不能为 0')
	if not 0 <= default_min_score <= 1:
		raise WenSuoRuntimeError('重排模型 model_meta.json 的 default_min_score 必须在 [0, 1] 内')
	return RerankMeta(
		max_length=max_length,
		query_max_tokens=query_max_tokens,
		passage_chars=passage_chars,
		temperature=temperature,
		bias=bias,
		default_min_score=default_min_score,
	)

def _positive_int(value: object, name: str) -> int:
	if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
		raise WenSuoRuntimeError(f'重排模型 model_meta.json 的 {name} 必须是正整数')
	return value

def _number(value: object, name: str) -> float:
	if isinstance(value, bool) or not isinstance(value, (int, float)):
		raise WenSuoRuntimeError(f'重排模型 model_meta.json 的 {name} 必须是数字')
	return float(value)

def rerank_model_identity(model_dir: str | Path) -> str | None:
	"""Rerank identity for doctor. 供 doctor 展示的重排身份。"""
	path = Path(model_dir) / 'model.onnx'
	if not path.is_file():
		return None
	return file_sha256(path)[:16]

def rerank_ready(model_dir: str | Path) -> bool:
	directory = Path(model_dir)
	return all((directory / name).is_file() for name in ('model.onnx', 'tokenizer.json', 'model_meta.json'))

class Reranker:
	def __init__(self, model_dir: str | Path, *, threads: int | None = None) -> None:
		directory = Path(model_dir)
		if not rerank_ready(directory):
			raise WenSuoRuntimeError(f'重排模型未就绪：{directory} 需要 model.onnx、tokenizer.json、model_meta.json')
		import onnxruntime as ort
		from tokenizers import Tokenizer as HFTokenizer

		self.model_dir = directory
		self.meta = load_rerank_meta(directory)
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
			raise WenSuoRuntimeError(f'重排模型没有输出：{directory / "model.onnx"}')
		self.output_name = outputs[0].name
		self.cls_id, self.sep_id = self._special_ids()
		self.identity = file_sha256(directory / 'model.onnx')[:16]

	def _special_ids(self) -> tuple[int, int]:
		cls = self.tokenizer.token_to_id('[CLS]')
		sep = self.tokenizer.token_to_id('[SEP]')
		if cls is None or sep is None:
			ids = self.tokenizer.encode('', add_special_tokens=True).ids
			if len(ids) < 2:
				raise WenSuoRuntimeError('重排模型 tokenizer.json 缺少 [CLS] / [SEP] 记号')
			cls, sep = ids[0], ids[-1]
		return int(cls), int(sep)

	def _encode_pair(self, query: str, passage: str) -> list[int]:
		query_limit = min(self.meta.query_max_tokens, max(1, self.meta.max_length - 3))
		query_ids = list(self.tokenizer.encode(query, add_special_tokens=False).ids)[:query_limit]
		available = max(0, self.meta.max_length - len(query_ids) - 3)
		passage_ids = list(self.tokenizer.encode(passage, add_special_tokens=False).ids)[:available]
		return [self.cls_id, *query_ids, self.sep_id, *passage_ids, self.sep_id]

	def _run(self, sequences: list[list[int]]) -> np.ndarray:
		max_length = max(len(sequence) for sequence in sequences)
		input_ids = np.zeros((len(sequences), max_length), dtype=np.int64)
		attention = np.zeros((len(sequences), max_length), dtype=np.int64)
		type_ids = np.zeros((len(sequences), max_length), dtype=np.int64)
		for row, sequence in enumerate(sequences):
			input_ids[row, : len(sequence)] = sequence
			attention[row, : len(sequence)] = 1
			sep_positions = [index for index, token in enumerate(sequence) if token == self.sep_id]
			if len(sep_positions) >= 2:
				# Query segment 0, passage segment 1. 查询段用 0，段落段用 1。
				type_ids[row, sep_positions[0] + 1 :] = 1
		feeds: dict[str, np.ndarray] = {'input_ids': input_ids, 'attention_mask': attention}
		if 'token_type_ids' in self.input_names:
			feeds['token_type_ids'] = type_ids
		return np.asarray(self.session.run([self.output_name], feeds)[0], dtype=np.float32)

	def score(self, query: str, passages: list[tuple[list[str], str]]) -> list[float]:
		if not passages:
			return []
		sequences = []
		for title_path, text in passages:
			passage = ' > '.join(title_path) + '\n' + text[: self.meta.passage_chars]
			sequences.append(self._encode_pair(query, passage))
		logits: list[float] = []
		batch_size = 16
		for start in range(0, len(sequences), batch_size):
			output = self._run(sequences[start : start + batch_size])
			if output.ndim == 1:
				output = output[:, None]
			if output.shape[1] == 1:
				batch_logits = output[:, 0]
			else:
				batch_logits = output[:, 1] - output[:, 0]
			logits.extend(float(value) for value in batch_logits)
		calibrated = 1.0 / (1.0 + np.exp(-(np.asarray(logits) / self.meta.temperature + self.meta.bias)))
		return [float(value) for value in calibrated]
__all__ = ['Reranker', 'RerankMeta', 'load_rerank_meta', 'rerank_model_identity', 'rerank_ready']
