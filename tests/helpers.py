"""测试辅助：写文件、构建假的 ONNX 向量模型与重排模型（A-19、A-21 的做法）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper
from tokenizers import Regex, Tokenizer, models, pre_tokenizers


def write(root: Path, relative: str, content: str, *, encoding: str = 'utf-8') -> Path:
	path = Path(root) / relative
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(content, encoding=encoding)
	return path


def _vocab(texts: list[str]) -> dict[str, int]:
	vocab = {'[UNK]': 0}
	for character in sorted({character for text in texts for character in text}):
		vocab[character] = len(vocab)
	return vocab


def _write_tokenizer(model_dir: Path, vocab: dict[str, int]) -> None:
	tokenizer = Tokenizer(models.WordLevel(vocab=vocab, unk_token='[UNK]'))
	tokenizer.pre_tokenizer = pre_tokenizers.Split(Regex('[\\s\\S]'), behavior='isolated')
	tokenizer.add_special_tokens(['[CLS]', '[SEP]'])
	tokenizer.save(str(model_dir / 'tokenizer.json'))


def _inputs() -> list:
	return [
		helper.make_tensor_value_info('input_ids', TensorProto.INT64, ['batch', 'seq']),
		helper.make_tensor_value_info('attention_mask', TensorProto.INT64, ['batch', 'seq']),
		helper.make_tensor_value_info('token_type_ids', TensorProto.INT64, ['batch', 'seq']),
	]


def _save(model_dir: Path, nodes: list, outputs: list) -> None:
	graph = helper.make_graph(nodes, 'fake-model', _inputs(), outputs)
	model = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 17)], producer_name='wensuo-tests')
	model.ir_version = 9
	onnx.checker.check_model(model)
	onnx.save(model, str(model_dir / 'model.onnx'))


def make_embedder_dir(model_dir: Path, texts: list[str]) -> Path:
	"""按字符查表的假向量模型：字符 one-hot，共享字符越多点积越高。"""
	model_dir.mkdir(parents=True, exist_ok=True)
	vocab = _vocab(texts)
	_write_tokenizer(model_dir, vocab)
	dimension = len(vocab) + 2
	table = np.eye(dimension, dtype=np.float32)
	nodes = [
		helper.make_node('Gather', ['table', 'input_ids'], ['gathered'], axis=0),
		helper.make_node('ReduceMean', ['gathered'], ['mean'], axes=[1], keepdims=0),
		helper.make_node('ReduceL2', ['mean'], ['norm'], axes=[1], keepdims=1),
		helper.make_node('Div', ['mean', 'norm'], ['embedding']),
	]
	initializers = [numpy_helper.from_array(table, name='table')]
	graph = helper.make_graph(
		nodes,
		'fake-embedder',
		_inputs(),
		[helper.make_tensor_value_info('embedding', TensorProto.FLOAT, ['batch', dimension])],
		initializer=initializers,
	)
	model = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 17)], producer_name='wensuo-tests')
	model.ir_version = 9
	onnx.checker.check_model(model)
	onnx.save(model, str(model_dir / 'model.onnx'))
	return model_dir


def make_reranker_dir(
	model_dir: Path,
	texts: list[str],
	weights: dict[str, float],
	*,
	default_min_score: float = 0.5,
) -> Path:
	"""按字符权重求和的假交叉编码器：包含正权重字符的段落得分高。"""
	model_dir.mkdir(parents=True, exist_ok=True)
	vocab = _vocab(texts)
	_write_tokenizer(model_dir, vocab)
	table = np.zeros((len(vocab) + 2, 1), dtype=np.float32)
	for character, weight in weights.items():
		if character not in vocab:
			raise KeyError(f'重排模型词表缺少字符：{character!r}')
		table[vocab[character], 0] = weight
	nodes = [
		helper.make_node('Gather', ['weights', 'input_ids'], ['gathered'], axis=0),
		helper.make_node('ReduceSum', ['gathered', 'axes'], ['logits'], keepdims=0),
	]
	graph = helper.make_graph(
		nodes,
		'fake-reranker',
		_inputs(),
		[helper.make_tensor_value_info('logits', TensorProto.FLOAT, ['batch', 1])],
		initializer=[
			numpy_helper.from_array(table, name='weights'),
			numpy_helper.from_array(np.array([1], dtype=np.int64), name='axes'),
		],
	)
	model = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 17)], producer_name='wensuo-tests')
	model.ir_version = 9
	onnx.checker.check_model(model)
	onnx.save(model, str(model_dir / 'model.onnx'))
	(model_dir / 'model_meta.json').write_text(
		json.dumps(
			{
				'max_length': 64,
				'query_max_tokens': 32,
				'passage_chars': 100,
				'temperature': 1.0,
				'bias': 0.0,
				'default_min_score': default_min_score,
			}
		),
		encoding='utf-8',
	)
	return model_dir


def yaml_config(**overrides) -> str:
	"""生成最小 ``wensuo.yaml``；默认关闭向量，避免测试依赖真实模型。"""
	base = {'dense': {'enabled': False}}
	base.update(overrides)
	lines: list[str] = []
	for key, value in base.items():
		lines.append(f'{key}: {json.dumps(value, ensure_ascii=False)}')
	return '\n'.join(lines) + '\n'


def run_cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
	import os
	import subprocess
	import sys

	command = [sys.executable, '-m', 'axc_wensuo.cli', '--root', str(root), *args]
	env = os.environ.copy()
	return subprocess.run(command, capture_output=True, text=True, env=env, timeout=120)
