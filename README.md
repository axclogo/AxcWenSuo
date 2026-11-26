# 文索 AxcWenSuo

[![CI](https://github.com/axclogo/AxcWenSuo/actions/workflows/ci.yml/badge.svg)](https://github.com/axclogo/AxcWenSuo/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)

**Offline, Chinese-first RAG engine with no server: any folder becomes a searchable knowledge base for humans and AI agents.**

中文优先、不起服务、目录即知识库的本地 RAG 引擎。人和程序共用同一个内核：命令行、Python API。

> 状态：`0.1.0`（Alpha）。核心检索、同步、引用、缺口清单、评测、CLI/API 已实现并有自动化测试；
> 默认向量模型的 Release（`models-v1`）尚未发布，见[准备模型](#准备模型)。

## 特性

- **目录即知识库**：放入、修改、删除文件后，下一次查询前自动增量同步；删掉索引目录后自动重建，结果一致。
- **中文关键词检索**：单字 + 相邻两字 + 标识符拆分的 BM25，中文与 `user_id` 这类英文标识符都能检索。
- **结果可追溯**：每条结果带文件路径、章节路径、行号 / 页码 / 幻灯片号 / 工作表行号。
- **分数可信**：可选重排分数经过温度/偏置校准，阈值有明确含义；未开启重排时分数只用于排序，不接受阈值。
- **知道“查不到”**：结果里列出知识库中不存在的标识符，并把空结果、缺标识符的查询记入缺口清单。
- **低资源、离线**：运行时只有 numpy / onnxruntime / tokenizers / PyYAML / filelock，不依赖 torch，不联网。
- **两种入口一个内核**：命令行文本/JSON、Python API 的检索结果完全一致。

## 安装

需要 Python 3.11 ~ 3.13，推荐 [uv](https://docs.astral.sh/uv/)。

从源码安装：

```bash
git clone https://github.com/axclogo/AxcWenSuo.git
cd AxcWenSuo
uv sync
uv run ws --version
```

需要解析 PDF / Word / Excel / PPT / HTML 时安装 `docs` 扩展（会带入 torch，体积较大）：

```bash
uv sync --extra docs
```

> `docs` 扩展使用 Docling。首次解析 PDF 时 Docling 会下载版面/表格模型（需要网络），之后离线可用；
> 也可以按 Docling 文档预置 `DOCLING_ARTIFACTS_PATH`。CI 不安装 Docling，相关测试会自动跳过。

发布到 PyPI 后可用：`pip install "axc-wensuo[docs]"`。

## 快速开始

```bash
# 1. 指定知识库根目录（也可在 ~/.config/wensuo/config.yaml 里登记 --kb 名字）
cd ~/Documents/notes

# 2. 看诊断：文档数、段数、专题、跳过/解析失败的文件
ws doctor

# 3. 检索
ws search "用户表的 user_id 字段"
ws search "部署 环境变量" --top-k 10 --scope 运维 --format json

# 4. 读原文 / 看单段
ws read "数据字典/用户.md" --section "用户表 > 字段说明"
ws show 3f2a9c0d1e4b5a67

# 5. 缺口清单：用户和 agent 都查不到的内容
ws gaps
```

`ws` 不带 `--root` / `--kb` 时使用全局配置的 `default`，没有则使用当前目录。

> 向量检索默认开启。若还没有准备模型，先在 `wensuo.yaml` 写入 `dense: {enabled: false}`，
> 否则带自动同步的命令会报“向量模型未就绪”。模型准备见下文[准备模型](#准备模型)。

### Python API

```python
from axc_wensuo import Filters, KnowledgeBase, SearchOptions

with KnowledgeBase('/path/to/docs') as kb:
	report = kb.sync()  # SyncReport
	result = kb.search('用户表的 user_id 字段', top_k=5, scope=['数据字典'])
	advanced = kb.search(
		'用户表',
		options=SearchOptions(filters=Filters(tags=['数据库'], file_types=['md']), max_chars=400),
	)
	doc = kb.read('数据字典/用户.md', section='用户表 > 字段说明')
	chunk = kb.show('3f2a9c0d1e4b5a67')
	docs = kb.list_docs(scope=['数据字典'])
	print(result.to_dict())  # 与 CLI --format json 完全相同
```

## 配置

知识库配置是可选的 `<root>/wensuo.yaml`（全部字段可省略）：

```yaml
name: notes                    # 显示名，默认根目录名
index_dir: null                # 索引目录，默认 <root>/.wensuo/
models_dir: null               # 默认 ~/.cache/wensuo/models/
threads: null                  # 默认 max(1, CPU 核数 // 2)
include: []                    # glob，相对根目录；**/ 可匹配零层或多层目录
exclude: []
max_file_mb: 50
scopes: {数据字典: 表结构与字段说明}
scope_rules: [{glob: "**/*报销*", scope: 财务}]
chunk: {max_chars: 1200, min_chars: 80}
lexical: {tokenizer: cjk_bigram, title_weight: 2, k1: 1.2, b: 0.5}
dense: {enabled: true, model: bge-small-zh-v1.5}
fusion: {w_lexical: 1.0, w_dense: 0.25, candidates: 50, max_per_doc: 3}
rerank: {model_dir: null, top_k: 30, min_score: null}
```

全局配置 `~/.config/wensuo/config.yaml`（Windows 为 `%APPDATA%\wensuo\config.yaml`）：

```yaml
default: notes
kbs:
  notes: ~/Documents/notes
  manuals: /data/manuals
```

同义词写在 `<root>/synonyms.yaml`，例如 `- [登录, 登陆, sign in]`；改完不需要重建索引。

完整字段、约束与行为以 [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) 为准。

### 专题（scope）

每篇文档恰好属于一个专题，按顺序取第一个命中的规则：

1. Markdown 文件头 YAML 的 `scope`；
2. `wensuo.yaml` 的 `scope_rules`（按顺序第一个匹配的 glob）；
3. 文件所在的第一级子目录名；
4. 根目录文件为 `_root`。

## 准备模型

向量检索默认开启，需要 `bge-small-zh-v1.5` 的 ONNX 导出（约 95 MB，512 维，CLS + L2 归一化）。

**当前 `models-v1` Release 尚未发布**，`ws models download` 会明确报错。两种方式：

1. 自己导出（需要临时安装 torch/transformers，不进入项目依赖）：

   ```bash
   uv run --with torch --with transformers --with huggingface_hub \
       python tools/export_embedder.py --output dist/models/bge-small-zh-v1.5
   ```

   然后在 `wensuo.yaml` 中设置 `models_dir` 指向 `dist/models`（目录结构为
   `<models_dir>/bge-small-zh-v1.5/{model.onnx,tokenizer.json}`）。

2. 暂时关闭向量，只用关键词检索：

   ```yaml
   dense: {enabled: false}
   ```

重排是可选项，默认关闭。开启时把 `rerank.model_dir` 指向一个包含 `model.onnx`、`tokenizer.json`、
`model_meta.json` 的目录（规格见需求文档第 7.4 节）。

## 命令行

```text
ws [--root PATH | --kb NAME] <命令> [选项]

ws search QUERY [--top-k 5] [--scope S]... [--path-prefix P] [--tag T]...
                [--file-type EXT]... [--modified-after YYYY-MM-DD] [--min-score F]
                [--max-chars 800] [--format text|json] [--no-sync] [--threads N]
ws read DOC_ID [--section "标题 > 子标题"] [--offset 0] [--max-chars 20000]
               [--format text|json] [--no-sync]
ws show CHUNK_ID [--format text|json]
ws docs [--scope S]... [--path-prefix P] [--format text|json] [--no-sync]
ws scopes [--format text|json]
ws sync [--threads N] [--format text|json]
ws doctor [--format text|json]
ws gaps [--limit 50] [--format text|json] [--clear]
ws eval QUERIES.jsonl [--modes lexical,dense,fused,rerank] [--k 5] [--format text|json]
ws models download [--force]
ws --version
```

退出码：`0` 成功；`1` `search` 无结果 / `read`、`show` 找不到对象；`2` 参数或配置错误；
`3` 运行错误（缺模型、缺扩展、索引损坏、锁超时等）。`--format json` 时错误也会以
`{"schema_version": 1, "error": {"code": 2, "message": "…"}}` 写到标准输出。

## 评测

评测集是 JSONL，每行一个对象：

```json
{"id": "q1", "query": "……", "relevant": [{"doc_id": "a/b.md", "title_path": ["标题"]}], "k": 5}
{"id": "q2", "query": "……", "no_answer": true}
```

```bash
ws eval eval/queries.jsonl --modes lexical,fused --k 5
```

输出 `hit_at_k`、`top1`、`mrr_at_k`、`answerable_return_rate`、`false_return_rate`。

## 开发

```bash
uv sync --dev
uv run ruff check .
uv run pytest
```

- 代码风格：Tab 缩进、单引号、行宽 120、方法/类之间 1 个空行（`ruff check` 见 [pyproject.toml](pyproject.toml)）。
- 公开测试语料在 [tests/fixtures/corpus](tests/fixtures/corpus)，由 [tools/make_fixtures.py](tools/make_fixtures.py) 生成。
- 非功能基准：[tools/bench.py](tools/bench.py)。
- 模型导出：[tools/export_embedder.py](tools/export_embedder.py)。
- 实现说明（索引结构、同步流程、检索流水线）见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
- 贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，开发红线见 [docs/CODE_POLICY.md](docs/CODE_POLICY.md)，行为准则见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 已知限制

- Alpha 阶段：解析器对 Docling 的支持依赖可选 `docs` 扩展；首次使用需要下载 Docling 模型，CI 默认不安装 Docling。
- 不做扫描件 OCR、旧版 `.doc/.xls/.ppt`、多用户、常驻服务、文件监听。
- 默认向量模型需要手动准备（见上文），在模型就绪前请关闭 `dense` 或只使用 `docs` 以外的关键词检索。
- 规模上限约 10 万段；相似度为暴力点积，不使用近似向量索引。
- 索引结构随版本变化；遇到“索引版本不兼容”时删除索引目录（默认 `<root>/.wensuo/`）后重新同步即可。

## 许可证

[Apache-2.0](LICENSE)。依赖许可：Docling（MIT）、bge-small-zh-v1.5（MIT）、onnxruntime（MIT）、
tokenizers（Apache-2.0）、filelock（Unlicense）、PyYAML（MIT）。
