# 变更日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.1.0] - 2026-11-26

首个 Alpha 版本：按 `docs/REQUIREMENTS.md` v1.0 一次性交付。

### Added

- 知识库门面 `KnowledgeBase`：打开/关闭、增量同步、检索、阅读、`show`、文档/专题列表、诊断、缺口、评测。
- 目录扫描与收纳规则：扩展名、include/exclude glob、大小上限、隐藏文件、索引目录、符号链接。
- 专题规则四级优先：文件头 YAML > `scope_rules` > 第一级子目录 > `_root`。
- 解析器：Markdown（标题/段落/列表/代码块/管道表格/文件头 YAML）、TXT（UTF-8 与 GB18030）、CSV/TSV；
  PDF/DOCX/PPTX/XLSX/HTML 通过可选 `docs` 扩展（Docling），带解析缓存。
- 切段：按标题分单元、段落贪心装箱、超长段落按句切分、代码块按行切分、表格单独成段并重复表头、
  短段按父章节路径合并、确定性 `chunk_id` 与 `index_text`。
- 中文分词 `cjk_bigram`（单字 + 相邻两字 + 标识符拆分）与可选 `jieba`，BM25 倒排索引。
- 向量检索：bge-small-zh-v1.5 ONNX、CLS + L2、长文本滑窗、float16 存储、查询指令前缀、向量缓存复用。
- 融合：加权 RRF、标识符精确命中分层、每文档限段、确定性排序。
- 可选重排：ONNX 交叉编码器、温度/偏置校准、按名次加权、阈值过滤。
- 缺口清单：空结果与缺失标识符记录、去重计数、按次数/时间排序。
- 评测：JSONL 评测集、`hit_at_k`/`top1`/`mrr_at_k`/`answerable_return_rate`/`false_return_rate`。
- 三种入口：`ws` 命令行（文本与 JSON）、MCP stdio（4 个工具）、Python API，JSON 输出完全一致。
- 工具：模型导出 `tools/export_embedder.py`、基准 `tools/bench.py`、语料生成 `tools/make_fixtures.py`。
- 测试：170+ 个自动化测试，覆盖需求 A-01 ~ A-25（Docling 相关用例在缺扩展时跳过）。
- 开源配套：README、贡献指南、行为准则、安全策略、CI（Linux/macOS/Windows × Python 3.11–3.13）。

### Known issues

- 默认向量模型的 `models-v1` Release 尚未发布；`ws models download` 会明确报错，需使用
  `tools/export_embedder.py` 本地导出，或临时关闭 `dense`。
- Docling 解析路径未进入默认 CI（依赖体积大），维护者需在本地安装 `docs` 扩展后运行 `tests/test_docling.py`。

[Unreleased]: https://github.com/axclogo/AxcWenSuo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/axclogo/AxcWenSuo/releases/tag/v0.1.0
