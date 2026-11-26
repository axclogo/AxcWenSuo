"""Default embedder release constants. 默认向量模型发布常量。"""

from __future__ import annotations

MODEL_NAME = 'bge-small-zh-v1.5'
RELEASE_TAG = 'models-v1'
RELEASE_BASE = f'https://github.com/axclogo/AxcWenSuo/releases/download/{RELEASE_TAG}/'
MODEL_FILES: dict[str, str | None] = {
	'model.onnx': None,
	'tokenizer.json': None,
}
"""文件名 -> 发布前填写的 SHA-256；``None`` 表示尚未发布。"""


def release_published() -> bool:
	return all(value is not None for value in MODEL_FILES.values())

def download_url(filename: str) -> str:
	return f'{RELEASE_BASE}{filename}'
