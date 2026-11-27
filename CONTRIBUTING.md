# 贡献指南

感谢参与文索（AxcWenSuo）。本项目是中文优先、离线、单机的 RAG 引擎，行为以
[docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) 为唯一依据；实现与文档冲突时先改文档、再改代码，
或在 Pull Request 中说明。

## 开发红线

动手前必读 [docs/CODE_POLICY.md](docs/CODE_POLICY.md)：代码质量、失败策略、模块边界与 Python 风格要求。
违反红线的 PR 不得合并。

## 开发环境

```bash
uv sync --dev   # 核心 + 测试
uv sync --dev --extra docs   # 需要解析 PDF/Office 时再加 docs
uv run ws --version
uv run pytest
```

可选：`uv run ruff check .`。

## 代码风格

- Tab 缩进、单引号、行宽 120、方法/类之间 1 个空行；`ruff` 规则 `E, F, I, B, UP`。
- 中文注释与文档字符串为主，标识符、命令、JSON 字段用英文。
- 库优先：检索、同步等能力先写在 `KnowledgeBase` / 各模块里；`cli.py` 只做参数解析和输出格式化。
- 显式失败：缺模型、缺可选依赖、配置非法时报错并给出修复方法，不静默回退。
- 确定性：同一文档、同一配置、同一模型的检索顺序必须一致；分数相同时按 `chunk_id` 升序。

## 测试

- 公开语料在 `tests/fixtures/corpus/`，由 `tools/make_fixtures.py` 生成，内容虚构。
- 需求第 11.1 节的 A-01 ~ A-25 都有对应自动化测试，测试名里包含行为描述；新增行为请同时补测试。
- 需要 Docling 的测试在未安装 `docs` 扩展时自动跳过（`pytest.mark.skipif`）。
- PDF 页码用例还需要 Docling 版面/表格模型资产；预置后设置 `WENSUO_TEST_DOCLING_MODELS=1` 再运行，
  否则跳过。Office/HTML 用例不依赖这些模型。
- 性能相关改动请运行 `python tools/bench.py`，并在 PR 中贴出前后对比。

## 常见改动

### 新增文档格式解析器

1. 在 `src/axc_wensuo/parsers/` 增加解析函数，返回 `ParsedDocument`（块带字符偏移与位置）。
2. 在 `parsers/__init__.py` 注册扩展名与 `parser_version_for()`。
3. 增加 `PARSER_VERSION` 常量；解析缓存键必须包含它。
4. 更新 `source.py` 中的扩展名集合（若是核心格式）或依赖 `docs` 扩展。
5. 补 `tests/test_parsers.py` 的用例与语料。

### 修改切段 / 分词行为

- 递增 `chunker.py` 的 `CHUNKER_VERSION` 或 `tokenizers.py` 的版本常量；同步会自动重建切段。
- 同步修改 `tests/test_chunker.py` / `tests/test_tokenizers.py`。
- 任何影响 `chunk_id` 的改动都会让旧索引全部重建，属预期行为。

### 修改索引结构

- 递增 `store.py` 的 `SCHEMA_VERSION`，并更新 `docs/REQUIREMENTS.md` 第 12 节。
- 旧索引会明确报错并提示删除索引目录，不做隐式迁移。
- 在 `tests/test_sync.py` 中补重建、增量、并发场景。

### 发布新版本的模型（`models-v1`）
1. 导出（torch 等只临时安装，不进项目依赖）：
   `uv run --no-project --python 3.13 --with torch --with transformers --with onnx --with onnxruntime --with huggingface_hub python tools/export_embedder.py --output dist/models/bge-small-zh-v1.5`
   脚本会校验 opset、单文件、可加载、输出名与形状，并打印两个文件的 SHA-256。同一 revision 重复导出结果逐字节相同。
   当前 revision 的哈希：`model.onnx` = `766903529093e202cd43e0f94a621c776b37085d36f48d3b42a623a308a0819e`（94,859,070 字节），
   `tokenizer.json` = `48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26`（439,125 字节）。
2. 先上传 Release，再改代码（顺序不能反，否则 `ws models download` 会下载到 404）：
   `gh release create models-v1 dist/models/bge-small-zh-v1.5/model.onnx dist/models/bge-small-zh-v1.5/tokenizer.json --title models-v1 --notes "bge-small-zh-v1.5 ONNX（opset 17）"`
3. 把两个哈希填进 `src/axc_wensuo/embedder_meta.py` 的 `MODEL_FILES`，更新 README 的模型说明和 CHANGELOG。
### 发布 Python 包
1. 一次性配置：在 PyPI 为项目 `axc-wensuo` 添加可信发布者（仓库 `axclogo/AxcWenSuo`、工作流 `release.yml`、环境 `pypi`），并在 GitHub 仓库设置里建 `pypi` 环境。
2. 改 `src/axc_wensuo/__init__.py` 的 `__version__` 和 CHANGELOG，提交后推送同名标签：`git tag v0.2.0 && git push origin v0.2.0`。
   工作流会校验标签与版本一致，然后构建并上传。
## 提交与 Pull Request

- 一个 PR 做一件事；包含行为变更时同步更新文档与 CHANGELOG。
- 提交信息用祈使句，说明“做了什么/为什么”，例如：`实现 FR-205 向量缓存复用`。
- PR 合并前确保：
  - [ ] `uv run pytest` 全绿
  - [ ] `uv run ruff check .` 通过
  - [ ] 新行为有测试，涉及需求条款的注明编号
  - [ ] 没有提交 `.wensuo/`、模型文件、私有评测数据

## 报告问题

- 功能缺陷与需求讨论用 GitHub Issues（模板见 `.github/ISSUE_TEMPLATE/`）。
- 安全问题请按 [SECURITY.md](SECURITY.md) 私下报告，不要开公开 Issue。

