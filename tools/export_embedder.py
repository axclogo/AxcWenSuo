#!/usr/bin/env python3
"""Export bge-small-zh-v1.5 to ONNX and verify the result. 导出 bge-small-zh-v1.5 为 ONNX 并校验结果。

Run with temporary dependencies only. 运行方式（依赖只临时安装，不进项目依赖）：
	uv run --no-project --python 3.13 --with torch --with transformers --with onnx --with onnxruntime \
		--with huggingface_hub python tools/export_embedder.py --output <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

MODEL_ID = 'BAAI/bge-small-zh-v1.5'
REVISION = '7999e1d3359715c523056ef9478215996d62a620'
OPSET = 17
DIMENSION = 512
INPUT_NAMES = ('input_ids', 'attention_mask', 'token_type_ids')
OUTPUT_NAME = 'embedding'
RELEASE_FILES = ('model.onnx', 'tokenizer.json')


class ExportError(Exception):
	"""The exported model does not meet FR-512. 导出结果不满足 FR-512。"""


def sha256(path: Path) -> str:
	digest = hashlib.sha256()
	with path.open('rb') as handle:
		for chunk in iter(lambda: handle.read(1024 * 1024), b''):
			digest.update(chunk)
	return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description='导出 bge-small-zh-v1.5 为 ONNX')
	parser.add_argument('--output', default='dist/models/bge-small-zh-v1.5')
	parser.add_argument('--model', default=MODEL_ID)
	parser.add_argument('--revision', default=REVISION)
	return parser.parse_args()


def _export_model(args: argparse.Namespace, output: Path) -> None:
	import torch
	from transformers import AutoModel

	class EmbeddingModel(torch.nn.Module):
		def __init__(self, model) -> None:
			super().__init__()
			self.model = model

		def forward(self, input_ids, attention_mask, token_type_ids):
			hidden = self.model(
				input_ids=input_ids,
				attention_mask=attention_mask,
				token_type_ids=token_type_ids,
			).last_hidden_state
			return torch.nn.functional.normalize(hidden[:, 0, :], p=2, dim=1)

	model = EmbeddingModel(AutoModel.from_pretrained(args.model, revision=args.revision)).eval()
	example = (
		torch.ones(2, 16, dtype=torch.int64),
		torch.ones(2, 16, dtype=torch.int64),
		torch.zeros(2, 16, dtype=torch.int64),
	)
	# dynamo=False: the TorchScript exporter honours opset 17 and writes one self-contained file.
	# dynamo=False：TorchScript 导出器遵守 opset 17，且只写一个自包含文件。
	torch.onnx.export(
		model,
		example,
		str(output / 'model.onnx'),
		input_names=list(INPUT_NAMES),
		output_names=[OUTPUT_NAME],
		dynamic_axes={name: {0: 'batch', 1: 'sequence'} for name in INPUT_NAMES} | {OUTPUT_NAME: {0: 'batch'}},
		opset_version=OPSET,
		do_constant_folding=True,
		dynamo=False,
	)


def verify_model(output: Path) -> None:
	"""Check opset, external data, loadability, output name and shape. 校验 opset、外部数据、可加载性、输出名与形状。"""
	import numpy as np
	import onnx
	import onnxruntime as ort
	from onnxruntime.capi import onnxruntime_pybind11_state as ort_errors

	path = output / 'model.onnx'
	model = onnx.load(str(path), load_external_data=False)
	opsets = {item.domain: item.version for item in model.opset_import}
	if opsets.get('', opsets.get('ai.onnx')) != OPSET:
		raise ExportError(f'opset 必须是 {OPSET}，实际 {opsets}')
	external = sorted(item.name for item in output.glob('model.onnx*') if item.name != 'model.onnx')
	if external:
		raise ExportError(f'导出目录里有外部权重数据文件：{", ".join(external)}')
	if any(tensor.data_location == onnx.TensorProto.EXTERNAL for tensor in model.graph.initializer):
		raise ExportError('model.onnx 引用了外部权重数据，必须是单文件')
	try:
		session = ort.InferenceSession(str(path), providers=['CPUExecutionProvider'])
	except (ort_errors.Fail, ort_errors.InvalidGraph, ort_errors.InvalidProtobuf, ort_errors.InvalidArgument) as exc:
		raise ExportError(f'onnxruntime 无法加载 model.onnx：{exc}') from exc
	if [item.name for item in session.get_inputs()] != list(INPUT_NAMES):
		raise ExportError(f'输入名必须是 {list(INPUT_NAMES)}')
	if [item.name for item in session.get_outputs()] != [OUTPUT_NAME]:
		raise ExportError(f'输出名必须是 [{OUTPUT_NAME!r}]，实际 {[item.name for item in session.get_outputs()]}')
	for batch, length in ((1, 5), (3, 9)):
		feeds = {
			'input_ids': np.ones((batch, length), dtype=np.int64),
			'attention_mask': np.ones((batch, length), dtype=np.int64),
			'token_type_ids': np.zeros((batch, length), dtype=np.int64),
		}
		result = session.run([OUTPUT_NAME], feeds)[0]
		if result.shape != (batch, DIMENSION):
			raise ExportError(f'输出形状必须是 [batch, {DIMENSION}]，batch={batch} 时得到 {list(result.shape)}')


def export(args: argparse.Namespace) -> dict[str, dict[str, object]]:
	from huggingface_hub import hf_hub_download

	output = Path(args.output)
	output.mkdir(parents=True, exist_ok=True)
	for stale in output.glob('model.onnx*'):
		stale.unlink()
	_export_model(args, output)
	shutil.copyfile(hf_hub_download(args.model, 'tokenizer.json', revision=args.revision), output / 'tokenizer.json')
	verify_model(output)
	return {name: {'sha256': sha256(output / name), 'size': (output / name).stat().st_size} for name in RELEASE_FILES}


def main() -> int:
	report = export(_parse_args())
	print(json.dumps(report, indent=2))
	print('\n校验通过。把上面两个文件的 sha256 填进 src/axc_wensuo/embedder_meta.py 的 MODEL_FILES。')
	return 0


if __name__ == '__main__':
	raise SystemExit(main())
