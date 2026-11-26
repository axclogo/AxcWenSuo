"""tools/export_embedder.py verification (FR-512). 导出脚本的校验逻辑。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper

SCRIPT = Path(__file__).resolve().parents[1] / 'tools' / 'export_embedder.py'


def _load_script():
	spec = importlib.util.spec_from_file_location('export_embedder', SCRIPT)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def _model(dimension: int = 512, output: str = 'embedding') -> onnx.ModelProto:
	inputs = [
		helper.make_tensor_value_info(name, TensorProto.INT64, ['batch', 'sequence'])
		for name in ('input_ids', 'attention_mask', 'token_type_ids')
	]
	nodes = [
		helper.make_node('Gather', ['table', 'input_ids'], ['gathered'], axis=0),
		helper.make_node('ReduceMean', ['gathered'], [output], axes=[1], keepdims=0),
	]
	graph = helper.make_graph(
		nodes,
		'fake',
		inputs,
		[helper.make_tensor_value_info(output, TensorProto.FLOAT, ['batch', dimension])],
		initializer=[numpy_helper.from_array(np.ones((2, dimension), dtype=np.float32), name='table')],
	)
	model = helper.make_model(graph, opset_imports=[helper.make_opsetid('', 17)])
	model.ir_version = 9
	return model


def _write(directory: Path, model: onnx.ModelProto, **kwargs) -> Path:
	directory.mkdir(parents=True, exist_ok=True)
	onnx.save(model, str(directory / 'model.onnx'), **kwargs)
	(directory / 'tokenizer.json').write_text('{}', encoding='utf-8')
	return directory


def test_valid_model_passes(tmp_path):
	_load_script().verify_model(_write(tmp_path / 'ok', _model()))


def test_wrong_opset_rejected(tmp_path):
	model = _model()
	model.opset_import[0].version = 18
	module = _load_script()
	with pytest.raises(module.ExportError, match='opset'):
		module.verify_model(_write(tmp_path / 'opset', model))


def test_external_data_rejected(tmp_path):
	directory = tmp_path / 'external'
	_write(directory, _model(), save_as_external_data=True, location='model.onnx.data', size_threshold=0)
	module = _load_script()
	with pytest.raises(module.ExportError, match='外部'):
		module.verify_model(directory)


def test_wrong_output_name_rejected(tmp_path):
	module = _load_script()
	with pytest.raises(module.ExportError, match='输出名'):
		module.verify_model(_write(tmp_path / 'name', _model(output='last_hidden_state')))


def test_wrong_dimension_rejected(tmp_path):
	module = _load_script()
	with pytest.raises(module.ExportError, match='形状'):
		module.verify_model(_write(tmp_path / 'dim', _model(dimension=384)))


def test_unloadable_model_rejected(tmp_path):
	model = _model()
	model.graph.node[1].op_type = 'NoSuchOperator'
	module = _load_script()
	with pytest.raises(module.ExportError, match='onnxruntime'):
		module.verify_model(_write(tmp_path / 'broken', model))
