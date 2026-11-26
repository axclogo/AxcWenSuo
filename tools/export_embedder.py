#!/usr/bin/env python3
"""Export bge-small-zh-v1.5 to ONNX. 导出 bge-small-zh-v1.5 为 ONNX。"""

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

def _parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description='导出 bge-small-zh-v1.5 为 ONNX')
	parser.add_argument('--output', default='dist/models/bge-small-zh-v1.5')
	parser.add_argument('--model', default=MODEL_ID)
	parser.add_argument('--revision', default=REVISION)
	return parser.parse_args()

def _export(args: argparse.Namespace) -> dict[str, dict[str, object]]:
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
			return torch.nn.functional.normalize(hidden[:, 0, :], p=2, dim=1)

	base_model = AutoModel.from_pretrained(args.model, revision=args.revision)
	model = EmbeddingModel(base_model).eval()
	example = (
		torch.ones(2, 16, dtype=torch.int64),
		torch.ones(2, 16, dtype=torch.int64),
		torch.zeros(2, 16, dtype=torch.int64),
	)
	torch.onnx.export(
		model,
		example,
		str(output / 'model.onnx'),
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
	return {
		name: {'sha256': sha256(output / name), 'size': (output / name).stat().st_size}
		for name in ('model.onnx', 'tokenizer.json')
	}

def main() -> int:
	report = _export(_parse_args())
	print(json.dumps(report, indent=2))
	print('\n把上面的 sha256 填进 src/axc_wensuo/embedder_meta.py 的 MODEL_FILES。')
	return 0


if __name__ == '__main__':
	raise SystemExit(main())
