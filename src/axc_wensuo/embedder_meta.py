"""默认向量模型的分发常量（FR-512）。

维护者执行 ``tools/export_embedder.py`` 导出 ``model.onnx`` 与 ``tokenizer.json``，上传到 GitHub
Release（标签 ``models-v1``），再把下载地址与两个文件的 SHA-256 填到这里。
在 Release 发布前，``ws models download`` 会明确报错，开发与测试使用 ``models_dir`` 指向本地导出文件。
"""

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
