#!/usr/bin/env python3
"""导出默认向量模型为 ONNX（FR-512）。

导出规格：fp32、不量化、opset 17；输入 ``input_ids``、``attention_mask``、``token_type_ids``
（int64，batch 与序列长度动态）；输出 ``embedding``（float32，``[batch, 512]``，即
``last_hidden_state[:, 0]`` 做 L2 归一化）。``tokenizer.json`` 直接取 HF 仓库同一 revision 的文件。

导出需要 torch 与 transformers，只在导出时临时安装，不进项目依赖::

	uv run --with torch --with transformers --with huggingface_hub \\
		python tools/export_embedder.py --output dist/models/bge-small-zh-v1.5

脚本会打印两个文件的 SHA-256 与大小，填入 ``src/axc_wensuo/embedder_meta.py`` 后即可发布
``models-v1`` Release。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

MODEL_ID = 'BAAI/bge-small-zh-v1.5'
REVISION = '7999e1d3359715c523056ef9478215996d62a620'


def sha256(path: Path) -> str:
	digest = hashlib.sha256()
	with path.open('rb') as handle:
		for chunk in iter(lambda: handle.read(1024 * 1024), b''):
			digest.update(chunk)
	return digest.hexdigest()


def main() -> int:
	parser = argparse.ArgumentParser(description='导出 bge-small-zh-v1.5 为 ONNX')
	parser.add_argument('--output', default='dist/models/bge-small-zh-v1.5')
	parser.add_argument('--model', default=MODEL_ID)
	parser.add_argument('--revision', default=REVISION)
	args = parser.parse_args()

	import torch
	from huggingface_hub import hf_hub_download
	from transformers import AutoModel

	output = Path(args.output)
	output.mkdir(parents=True, exist_ok=True)

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
			embedding = hidden[:, 0, :]
			return torch.nn.functional.normalize(embedding, p=2, dim=1)

	base_model = AutoModel.from_pretrained(args.model, revision=args.revision)
	base_model.eval()
	model = EmbeddingModel(base_model).eval()

	batch, length = 2, 16
	example = (
		torch.ones(batch, length, dtype=torch.int64),
		torch.ones(batch, length, dtype=torch.int64),
		torch.zeros(batch, length, dtype=torch.int64),
	)
	onnx_path = output / 'model.onnx'
	torch.onnx.export(
		model,
		example,
		str(onnx_path),
		input_names=['input_ids', 'attention_mask', 'token_type_ids'],
		output_names=['embedding'],
		dynamic_axes={
			'input_ids': {0: 'batch', 1: 'sequence'},
			'attention_mask': {0: 'batch', 1: 'sequence'},
			'token_type_ids': {0: 'batch', 1: 'sequence'},
			'embedding': {0: 'batch'},
		},
		opset_version=17,
		do_constant_folding=True,
	)
	tokenizer_path = hf_hub_download(args.model, 'tokenizer.json', revision=args.revision)
	shutil.copyfile(tokenizer_path, output / 'tokenizer.json')

	report = {
		name: {'sha256': sha256(output / name), 'size': (output / name).stat().st_size}
		for name in ('model.onnx', 'tokenizer.json')
	}
	print(json.dumps(report, indent=2))
	print('\n把上面的 sha256 填进 src/axc_wensuo/embedder_meta.py 的 MODEL_FILES。')
	return 0


if __name__ == '__main__':
	raise SystemExit(main())
