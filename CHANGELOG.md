# 变更日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与
[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

按 `docs/REQUIREMENTS.md` v1.1。

### Changed
- 切段版本 2（FR-401~406）：空标题单元并入首个子章节；表格前的短说明作为表格标题随表头重复；表格段参与短段合并；
  不跨工作表合并。私有语料上短于 80 字的段从 1,336 降到 8。
- 检索默认参数：BM25 `b=0.5`、RRF 常数 20、`w_dense=0.25`，新增关键词头名加分（FR-602，借鉴 qmd）。
- 关键词召回前去掉查询里的疑问词（FR-504），避免索引类文档靠“怎么”“哪些”排到第一。
- `show` JSON 去掉 `rank`，增加 `schema_version`、`score`、`sources`；结果元素增加 `expanded`。
- `path_prefix` 区分大小写；`missing_identifiers` 按第 4 节子串加边界判断。

### Added
- 重排模型的 `model_meta.json` 可以不带 `default_min_score`，阈值改由 `rerank.min_score` 或 `min_score` 提供（FR-611）。
- CI：失败用例写成注解；新增 docs 扩展（Docling）测试任务；新增打标签发布 PyPI 的工作流。
- `--expand-section` / `SearchOptions(expand_section=True)`：结果扩展为命中段所在章节（FR-605，借鉴 haiku.rag）。
- `tools/export_embedder.py` 导出后校验 opset、单文件、可加载、输出名与形状。

### Removed
- MCP 服务（`ws mcp`、`mcp` 扩展）；需要的调用方基于 Python API 自行封装。
- 未使用的 `open_knowledge_base`。

### Fixed
- Windows 下命令行输出中文时按代码页编码崩溃（`UnicodeEncodeError`）；标准输出和错误输出固定为 UTF-8。
- 开启向量时任何过滤条件都报 `no such column: c.scope`。
- 坏符号链接、无读权限文件、超长 CSV 单元格导致整次同步失败；现在记为跳过或解析失败，其余文件照常同步。
- 修改 `scope_rules` 后专题不更新；删除或排除的文件失败记录残留；解析失败文件每次重算哈希。
- DOCX 标题级别被抬高；`## C#` 标题被截成 `C`；单列管道表格不识别；多行 CSV 记录起始行号错误；
  配置接受 NaN/Infinity；`ws gaps --limit` 不校验；Docling 缓存残留；向量末窗口重复计数。

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

### Changed

- 检索、阅读、列表 API 使用 `SearchOptions` / `ReadOptions` / `ListDocsOptions` 聚合可选参数，每个函数参数不超过 5 个。
- 失败策略收紧：只有解析失败（`ParseError`）会被隔离并上报；缺模型、配置错误、写入失败、缓存损坏直接抛错，不做兜底或静默重试。
- 代码规范：注释中英双语、单行；方法/类之间 1 个空行；移除 MCP 1.x/2.x 双兼容与多余防御分支。

### Known issues

- 默认向量模型的 `models-v1` Release 尚未发布；`ws models download` 会明确报错，需使用
  `tools/export_embedder.py` 本地导出，或临时关闭 `dense`。
- Docling 解析路径未进入默认 CI（依赖体积大），维护者需在本地安装 `docs` 扩展后运行 `tests/test_docling.py`。

[Unreleased]: https://github.com/axclogo/AxcWenSuo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/axclogo/AxcWenSuo/releases/tag/v0.1.0
