# 实现说明

本文给贡献者看：需求如何落到代码、索引里有什么、哪些不变量不能破坏。行为以
[REQUIREMENTS.md](REQUIREMENTS.md) 为准，本文只解释实现选择。

## 模块

| 模块 | 职责 |
| --- | --- |
| `config.py` | 全局配置与 `wensuo.yaml` 的加载、校验、合并（命令行 > 文件 > 默认值） |
| `errors.py` | `ConfigError`（退出码 2）、`WenSuoRuntimeError`（退出码 3） |
| `models.py` | 对外 dataclass；`to_dict()` 即第 8.3 节 JSON |
| `source.py` | 目录扫描、收纳规则、专题规则、变化检测、SHA-256 |
| `parsers/` | Markdown / TXT / CSV-TSV、可选 Docling；产出带偏移的 `Block` |
| `chunker.py` | 按标题分单元、装箱、拆分、表格、短段合并、`chunk_id` |
| `tokenizers.py` | `cjk_bigram`、`jieba`、标识符提取与命中判断 |
| `lexical.py` | BM25 打分（倒排在 SQLite） |
| `dense.py` | ONNX 编码、滑窗、float16 向量、暴力点积召回 |
| `rerank.py` | ONNX 交叉编码器、校准、阈值 |
| `search.py` | 过滤 → 双路召回 → 加权 RRF → 标识符分层 → 重排 → 限段 → top-k |
| `synonyms.py` | 查询同义词扩展（只影响关键词一路） |
| `store.py` | SQLite（WAL）、schema、文件锁、事务 |
| `gaps.py` | 缺口清单记录与读取 |
| `evaluate.py` | `ws eval` 指标 |
| `kb.py` | `KnowledgeBase` 门面：同步、检索、阅读、诊断 |
| `cli.py` / `mcp_server.py` | 入口层，不含检索逻辑 |

## 索引目录

```text
<index_dir>/
  index.sqlite   # WAL 模式
  parsed/        # Docling 解析缓存：<文件SHA256>-<Docling版本>.json
  lock           # filelock 文件锁
```

`index.sqlite` 的主要表：

| 表 | 内容 |
| --- | --- |
| `meta` | schema 版本、`chunk_sig`、`dense_sig`、`built_at`、段数、`avgdl` |
| `docs` | 文档行：标题、专题、标签、别名、大小、mtime、SHA-256、解析器版本、段数 |
| `chunks` | 段：`chunk_id`、正文、`index_text`、`title_path`、`location`、加权长度 `dl`、float16 向量 |
| `terms` / `postings` | 倒排表：`(term_id, chunk_pk) → tf_body, tf_meta` |
| `vector_cache` | 向量缓存：`(index_text 的 SHA-256, 模型身份) → float16 向量`，跨文档复用 |
| `doc_tags` / `chunk_ident` / `ident_run` | 标签过滤、标识符命中分层、全库标识符存在性 |
| `failures` | 解析失败缓存：原因、SHA-256、解析器版本、大小、mtime |
| `gaps` | 缺口清单：查询、过滤条件、入口、次数、首末时间 |

设计取舍：

- 不用 SQLite FTS5（默认不切中文），倒排表自实现；`df` 在查询时 `COUNT(*)`，避免增量同步时维护计数。
- 查询只读取查询词对应的倒排行，内存占用与库大小基本无关；`avgdl`、段数在元数据里。
- 向量直接存在 `chunks.vec`，查询时按过滤条件批量读出、`np.frombuffer(float16) → float32` 后矩阵乘。
- 标识符存在性用 `ident_run`（全库去重、引用计数），避免每次查询全表扫描 `index_text`。

## 同步流程

`KnowledgeBase._sync()`（持文件锁）：

1. 扫描目录，得到文件清单与被跳过清单。
2. 读取 `docs`，`scan - docs` 即删除；删除文档、段、倒排、向量、标签、解析缓存。
3. 判断是否全量重建：`chunk_sig`（切段 + 分词配置 + 版本）变化 → 重新切段；`dense_sig`（模型身份）
   变化或存在缺向量的段 → 重新计算向量。
4. 逐文件：大小/mtime 相同则跳过；不同则算 SHA-256，哈希相同只更新时间戳；哈希变化才重新解析。
5. 解析失败记录到 `failures`；同文件哈希 + 同解析器版本不再重试。
6. 每个文件的增删改在一个 `BEGIN IMMEDIATE` 事务内完成；失败文件写入失败记录并删除旧段。
7. 收尾更新 `chunk_sig`、`dense_sig`、`built_at`、段数、`avgdl`。

不变量：

- 文件是唯一事实来源；索引可随时删除重建，结果（含 `chunk_id` 与顺序）一致。
- 同一文档、同一配置、同一模型下 `chunk_id` 只由内容决定，不受其他文件影响。
- 读者在 WAL 下看到已提交数据；拿不到写锁时用旧索引查询并标记 `stale`。

## 检索流水线

严格按第 7.3 节顺序：

1. 过滤条件转 SQL（`Filters.where()`），在召回前生效。
2. 关键词 BM25 与向量点积各取 `candidates`（默认 50）。
3. 加权 RRF：`Σ w_i / (60 + rank_i)`。
4. 查询含标识符时按“段内命中的不同标识符个数”分层；层内按融合分排序。
5. 配置了重排时取前 `rerank.top_k`，`p = sigmoid(logit / temperature + bias)`，
   `final = w × s_ret + (1 − w) × p`（`w` 按名次 0.75/0.60/0.40），丢弃 `p < min_score`。
6. 层内排序、每文档最多 `max_per_doc` 段、截取 `top_k`、从 1 编号。
7. 计算 `missing_identifiers`（全库 `ident_run`），结果为空或缺标识符时写缺口清单。

所有排序在分数相同时按 `chunk_id` 升序，保证确定性。

## 线程与并发

- 一个 `KnowledgeBase` 对应一个线程（文档明示）；MCP 工具声明为 `async`，在事件循环线程内执行，
  与创建连接同线程。
- 写操作由索引目录下的 `lock` 文件串行化：自动同步不等待，拿不到锁返回 `stale: true`；
  `ws sync` 最多等待 60 秒。
- 数据库初始化对短暂的锁冲突做有限重试；WAL 允许读写并行。

## 扩展点

- **解析器**：实现 `parse_*` 返回 `ParsedDocument`，注册扩展名与 `parser_version_for()`；块必须带
  字符偏移，行号/页码等位置按 `Location` 表达。
- **分词器**：实现 `Tokenizer` 接口并加入 `TOKENIZER_NAMES`；查询与文档必须走同一分词器。
- **重排模型**：只依赖 `model.onnx`、`tokenizer.json`、`model_meta.json`，不写死模型架构。
- **向量模型**：默认模型身份 = 文件名 + 两个文件的 SHA-256；换模型会自动重算全部向量。

## 测试策略

- 单元测试覆盖分词、解析、切段、过滤、指标；集成测试用临时目录跑真实同步/检索。
- 假模型：用 `onnx` 在运行时生成固定行为的向量模型与重排模型（`tests/helpers.py`），
  不把二进制模型放进仓库。
- 公开语料：`tests/fixtures/corpus/`（虚构内容），由 `tools/make_fixtures.py` 生成。
- Docling 相关用例在未安装 `docs` 扩展时跳过；CI 不安装 Docling（体积与模型下载成本）。
- 并发：两个进程同时同步、持锁查询 `stale`、索引完整性检查。
