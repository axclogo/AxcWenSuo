# 文索 AxcWenSuo —— 需求文档 v1.1

> 项目名 `AxcWenSuo`，发布包名 `axc-wensuo`，导入名 `axc_wensuo`，命令行 `ws`，知识库配置文件 `wensuo.yaml`。文：文档，索：检索与索引。
>
> 本文档是开发的唯一依据，读者包括人和 AI 开发者。文中"必须"是硬性要求，"默认"是可配置的初始值，"不做"是明确排除的范围。本文没有写到的行为，按第 3 节设计原则处理；仍无法判断的，在代码中显式报错，不自行发明行为。

---

## 0. 目的（一句话）

**把任意一个本地文档目录变成可离线检索的知识库：不起服务、没有构建步骤、中文检索准确、每条结果可追溯到原文位置；人用命令行查，程序和 AI agent 用 Python API 或命令行 JSON 查。**

一次性交付，不分期。

---

## 1. 背景

### 1.1 要解决的问题

个人的知识（技术文档、数据字典、操作手册、规章制度、笔记）分散在 Markdown、PDF、Word、Excel 里，需要：

- **人**：随手搜到答案和出处。
- **agent**（Claude Code、Kiro、Codex 等）：在写代码、排查问题时按需检索，拿到可引用的原文。

使用形态是**单机、单用户**：知识库就是本机上的一个目录。多人共享时，共享的是文档目录本身（例如放进 git 或网盘），每台机器各自建索引。

### 1.2 现有方案的缺口

| 方案 | 主要问题 |
| --- | --- |
| RAGFlow、QAnything、WeKnora、MaxKB、Dify 等平台 | 要部署服务（Elasticsearch、向量库、MySQL……），不适合随手用、离线用、放进 git 管 |
| haiku.rag | 解析和引用很强，但全文检索没有中文语言选项；目录同步要起常驻 ingester 服务；默认模型依赖 Ollama 服务；依赖重 |
| txtai | 引擎成熟、纯库、可增量，但只是引擎：没有目录同步、文档解析、引用、命令行，默认模型是英文的 |
| qmd | agent 体验好，但中文只能精确短语匹配，改了文档要手动 `update`/`embed`，重排和扩写模型对单核电脑偏重 |

### 1.3 已有实测（决定设计方向）

在一个 141 篇、2,515 段的中文技术文档库上，用 32 条 agent 实际发出的查询，由大模型按 0~3 分打分：

| 检索方式 | 前 k 条里有相关 | 第一条相关 |
| --- | --- | --- |
| jieba 分词 BM25 | 93.8% | 78.1% |
| 单字 + 相邻两字 + 标识符拆分 BM25 | **96.9%** | **84.4%** |
| 通用中文向量（bge-small-zh） | 75.0% | 37.5% |
| 关键词 + 向量按名次融合 | 87.5% | 71.9% |

结论：
1. agent 的查询多是"英文标识符 + 中文关键词"的词袋，**关键词一路是主力**，中文分词质量决定上限。
2. 向量和重排能补同义、口语表达，但**不能简单融合**，否则会把关键词的精确命中排下去。
3. 知识库里本来就没有的内容，任何算法都救不回来，需要把"无答案"反馈给人去补文档。

---

## 2. 目标与不做

### 2.1 目标（每条都有对应验收，见第 11 节）

| 编号 | 目标 | 验收 |
| --- | --- | --- |
| G1 | 不起服务：作为 Python 库和命令行运行在调用方进程里，没有常驻进程 | A-01 |
| G2 | 目录即知识库：放入、修改、删除文件后，下一次查询前自动增量同步，用户不需要执行任何构建命令 | A-03 ~ A-06 |
| G3 | 中文检索准确：关键词检索效果不低于第 1.3 节"单字 + 相邻两字 + 标识符拆分"基线；中英混排、代码标识符可检索 | A-07 ~ A-09、Q-01、Q-02 |
| G4 | 按专题检索：每篇文档属于一个专题（scope），检索可按专题、路径、标签、文件类型过滤 | A-10、A-11 |
| G5 | 结果可追溯：每条结果带文件路径、章节路径、行号或页码或工作表行号 | A-12 ~ A-14 |
| G6 | 人和程序共用一个内核：命令行（文本、JSON）、Python API 两个入口，检索结果完全一致 | A-15 ~ A-17 |
| G7 | 低资源：单核 CPU 可用，运行时不依赖 torch 和 GPU，不需要网络（模型下载后） | N-01 ~ N-08 |
| G8 | 知道"查不到"：结果里明确标出查询中的标识符是否在知识库出现过；配置了重排模型时，低于阈值返回空；查不到的查询记入缺口清单 | A-18 ~ A-21、Q-03 |

### 2.2 不做

以下内容不实现，代码里也不预留空接口：

- 大模型问答（`ask`）、终端对话界面、网页界面。
- MCP 服务。本项目是检索引擎；需要 MCP 的调用方基于 Python API 或命令行 JSON 自行封装。
- 后台常驻服务、文件监听（watch）、多用户、权限。
- 自动给文档归类专题（专题只由第 5.3 节规则决定）。
- 扫描件和图片的文字识别（OCR）。扫描版 PDF 解析出空文本时，按"解析结果为空"记录。
- `.doc`、`.xls`、`.ppt` 等旧版二进制 Office 格式。
- LanceDB 等外部存储、近似向量索引。规模上限是 10 万段。
- 训练模型。本项目只使用模型。
- 自带重排模型。重排是可选组件，用户自己提供模型目录（格式见第 7.4 节）。

---

## 3. 设计原则

| 原则 | 含义 |
| --- | --- |
| 库优先 | 所有能力先是 Python 函数；命令行只做参数解析和输出格式化，不含检索逻辑 |
| 目录是唯一事实来源 | 索引是派生物；删掉索引目录，下次查询自动重建，结果与重建前一致 |
| 显式失败 | 缺模型、缺可选依赖、配置非法时报错并给出修复方法；不静默回退到其他方案。单个文件读不了或解析失败不影响整库（其余文件照常同步，`search`/`read`/`docs` 照常可用），但必须记录并在 `ws doctor` 中显示（规则见 FR-116、FR-206） |
| 确定性 | 同一份文档、同一配置、同一模型，检索结果顺序完全一致。所有排序在分数相同时按 `chunk_id` 升序打破平局 |
| 依赖分层 | 核心依赖只有 numpy、onnxruntime、tokenizers、PyYAML、filelock；PDF 与 Office 解析放在 `docs` 扩展 |

---

## 4. 术语

| 术语 | 定义 |
| --- | --- |
| 知识库（KB） | 一个根目录（root）及其下被收纳的文件 |
| 文档（doc） | 一个被收纳的文件。`doc_id` = 相对根目录的路径，统一用 `/` 分隔，做 Unicode NFC 归一 |
| 专题（scope） | 文档所属的分类名，每篇文档恰好一个，规则见第 5.3 节 |
| 段（chunk） | 检索和返回的最小单位，由切段规则产生 |
| 章节路径（title_path） | 段所在的标题链，例如 `["部署指南", "环境变量"]` |
| 标识符（identifier） | 提取：查询做 NFKC 归一并转小写后，取连续的 `[a-z0-9_.\-]` 串，去掉首尾的 `.` 和 `-`；剩余串满足"长度 ≥ 3 且含字母"或"含 `_` 或 `.`"即为标识符，去重后按首次出现顺序排列。例：`查 user_id.` → `user_id`；`v2.1`、`max_pt` 是标识符，`id`、`42` 不是 |
| 标识符命中 | 对段的 `index_text` 做 NFKC 归一并转小写后，标识符作为子串出现，且出现位置的前一个和后一个字符都不是 `[a-z0-9_]`（或位于文本边界）。例：`user_id` 命中 `表 user_id 字段`，不命中 `user_ids`；`user` 不命中 `user_id` |
| 索引目录 | 存放索引的目录，默认 `<root>/.wensuo/` |

---

## 5. 知识库与收纳

### 5.1 打开知识库

- **FR-101** 命令行用 `--root <路径>` 或 `--kb <名字>` 指定知识库。两者都没给时：全局配置有 `default` 就用它，否则用当前工作目录。两者都给时报错。
- **FR-102** 全局配置文件：`~/.config/wensuo/config.yaml`（Windows：`%APPDATA%\wensuo\config.yaml`），格式：

  ```yaml
  default: notes
  kbs:
    notes: ~/Documents/notes
    manuals: /data/manuals
  ```

- **FR-103** 知识库配置文件：`<root>/wensuo.yaml`，可选，全部字段可省略，完整字段见第 9 节。未知字段报错。
- **FR-104** 索引目录默认 `<root>/.wensuo/`，可用配置 `index_dir` 改到别处。根目录不可写且未配置 `index_dir` 时报错，提示配置 `index_dir`。

### 5.2 收纳规则

- **FR-111** 默认收纳扩展名：`.md`、`.markdown`、`.txt`、`.csv`、`.tsv`（核心）；`.pdf`、`.docx`、`.pptx`、`.xlsx`、`.html`、`.htm`（需要 `docs` 扩展）。扩展名不区分大小写。
- **FR-112** `include` / `exclude` 是 glob 列表，相对根目录匹配，`**/` 可匹配零层或多层目录。配置了 `include` 时只收纳匹配的文件；`exclude` 优先于 `include`。
- **FR-113** 总是跳过：以 `.` 开头的文件和目录、索引目录、`wensuo.yaml`、`synonyms.yaml`、`node_modules/`、`__pycache__/`，以及超过 `max_file_mb`（默认 50）的文件。
- **FR-114** 需要 `docs` 扩展的文件，在未安装扩展时不报错退出，记为"跳过：缺少 docs 扩展"，在 `ws doctor` 和同步报告中显示。
- **FR-115** 符号链接：跟随文件链接，不跟随目录链接。
- **FR-116** 扫描时取不到文件信息的条目（例如指向不存在目标的符号链接），记为"跳过：无法读取文件信息：<原因>"，不中断同步。

### 5.3 专题（scope）规则

每篇文档的专题按以下顺序取第一个命中的：

1. Markdown 文件头 YAML（`---` 包围）中的 `scope` 字段。
2. `wensuo.yaml` 中 `scope_rules` 列表，按顺序第一个 glob 匹配的规则。
3. 文档所在的第一级子目录名。
4. 文档直接在根目录下时，专题为 `_root`。

- **FR-121** 专题名区分大小写，比较时做 Unicode NFC 归一。
- **FR-122** `wensuo.yaml` 的 `scopes` 字段可给每个专题写一句描述，在 `ws scopes` 中展示。没有描述的专题照常可用。

### 5.4 文档元信息

- **FR-131** Markdown 文件头 YAML 中的 `title`、`tags`（字符串或字符串列表）、`aliases`、`scope` 进入索引；其他字段忽略。文件头 YAML 解析失败时，把它当正文处理并记录警告。
- **FR-132** 文档标题：文件头 `title` > 第一个一级标题 > 文件名（去扩展名）。
- **FR-133** `title` 和 `aliases` 参与关键词索引（第 7.1 节）。

---

## 6. 同步、解析与切段

### 6.1 增量同步

- **FR-201** `search`、`read`、`list_docs` 默认先同步再执行；`--no-sync`（命令行）或 `sync=False`（API）跳过同步。`ws sync` 只做同步。
- **FR-202** 判断变化：先比较文件大小和修改时间（纳秒）；有差异再算内容 SHA-256；哈希没变只更新时间戳，变了才重新解析、切段、计算向量。此规则同样适用于解析失败的文件（FR-206）。
- **FR-203** 删除的文件，同步时把它的段、向量、解析缓存一并删除。删除或被 `include`/`exclude` 排除的文件，它的解析失败记录也一并删除，不再出现在 `ws doctor` 中。文件修改后，旧内容的 Docling 解析缓存在没有其他文档使用时删除。
- **FR-204** 以下任一项变化时，全部文档重新切段：切段配置、分词配置（含 `title_weight`）、`scope_rules`、本程序的切段版本号（代码常量，当前为 2）。向量模型身份（模型名 + 文件 SHA-256）变化时，全部向量重算。
- **FR-205** 向量按"段索引文本的 SHA-256 + 向量模型身份"缓存，文本没变的段不重算。
- **FR-206** 解析失败的文件记录失败原因、文件大小、修改时间、文件哈希和解析器版本（Docling 版本加本程序适配层版本，或本程序解析器版本常量）。下次同步时，解析器版本不变且大小和修改时间都不变，直接沿用失败记录，不读文件、不算哈希；大小或修改时间变了再算哈希，哈希也没变则只更新记录里的大小和修改时间，仍不重试。以下都算解析失败：解码失败、CSV/TSV 格式错误（含单元格超过 Python `csv` 模块的字段长度上限）、文件读不了（例如没有读权限）。读不了的文件记不到哈希，每次同步都重试。缺少 `docs` 扩展而跳过的文件，在安装扩展后的下一次同步中解析。内容为空（或解析结果为空）的文件记为"解析结果为空"，不产生段。
- **FR-207** 并发：写入（同步）用索引目录下的文件锁 `lock` 串行化；每个文件的增删改在一个 SQLite 事务内完成，读者只看到已提交的数据。
  - `search`/`read`/`docs` 的自动同步拿不到锁时，不等待，直接用已有索引查询并标记 `stale: true`；索引从未建立过时报运行错误（退出码 3），提示稍后重试。
  - `--no-sync` 时 `stale` 为 `false`。
  - `ws sync` 拿不到锁时最多等待 60 秒，超时报运行错误（退出码 3）。
- **FR-208** 同步报告：新增、修改、删除、跳过、失败的文件数，以及耗时。命令行在标准错误输出打印；同步文件数超过 20 个时显示进度。
- **FR-209** 向量计算线程数：默认 `max(1, CPU 核数 // 2)`，可用 `--threads` 或配置 `threads` 指定。

### 6.2 解析

- **FR-301** Markdown：保留标题层级、段落、列表、表格（管道表格）、代码块、分隔线。代码块内的 `#` 不当标题。
  - 标题：ATX 标题（`#` 到 `######` 后跟空白）。标题文本去掉首尾空白；行尾的闭合 `#` 序列只有前面有空白时才去掉，紧贴文字的 `#` 是标题的一部分（`## C#` 的标题是 `C#`，`## 结尾 ##` 的标题是 `结尾`）。
  - 管道表格：一行含 `|`，下一行是分隔行（每个单元格都是 `:?-+:?`）即为表格开始；单列表格（`| 名称 |` / `| --- |`）同样识别。
  - 分隔线：一行只有 3 个及以上同一种 `-`、`*`、`_`（可夹空白，行首最多 3 个空格），且不在代码块和文件头 YAML 内。不支持 Setext 标题，段落下一行的 `---` 也按分隔线处理。
- **FR-302** TXT：按空行分段落，没有标题。编码先试 UTF-8（含 BOM），失败再试 GB18030；都失败则记为解析失败。
- **FR-303** CSV/TSV：整个文件是一张表，第一行是表头。编码规则同 TXT。一条记录跨多行（引号内换行）时，它的行号区间从记录的第一行到最后一行。
- **FR-304** PDF、DOCX、PPTX、XLSX、HTML（`docs` 扩展）：用 Docling 解析为结构化文档，保留标题层级、段落、表格（含表头）、页码（PDF）、幻灯片号（PPTX）、工作表名和行号（XLSX）。XLSX 每个工作表是一张表，第一行是表头。标题级别取 Docling 标题条目自身的级别（`level`，缺失时为 1），不取它在文档树中的深度。
- **FR-305** Docling 的解析结果按"文件 SHA-256 + Docling 版本 + 适配层版本"缓存到索引目录，切段配置变化时不重新解析。适配层版本是本程序常量，转换规则变化时加 1。
- **FR-306** `read` 返回的全文：Markdown 和 TXT 返回原文；其他格式返回由解析结果转换出的 Markdown。
- **FR-307** `read --section`：参数按 ` > ` 拆成标题列表，与文档各标题的完整标题链做前缀精确匹配（NFC，去首尾空白），取文档内第一个命中的标题，返回从该标题行到下一个同级或更高级标题之前的内容（含全部子章节）。找不到时退出码 1。
- **FR-308** `read` 的 `offset`、`max_chars` 以码点计，相对于返回文本（整篇或所选章节）的起点；`next_offset` = `offset + 实际返回码点数`，已到末尾时为 `null`。

### 6.3 切段（切段版本 2）

参数默认值：`max_chars = 1200`，`min_chars = 80`。字符数按 Unicode 码点计。段长度 = `text` 的码点数（含标题行、块之间的换行）。

**设计理由**：BM25 的长度归一化偏好短段。版本 1 在 141 篇私有文档上切出 4,390 段，其中 1,336 段短于 80 字、713 段短于 30 字，主要是只有标题行的段（父标题与第一个子标题之间没有内容）和与表格分开的标签行（如 `**status 状态**：`）。这些碎片排在前面，又占满 `max_per_doc` 名额，把含答案的字段表挤出结果。版本 2 让标题作为章节上下文跟随其内容，让表格说明跟随表格，短于 `min_chars` 的段只在找不到可并入的相邻段时保留；同一语料切出 2,944 段，短于 80 字的 8 段、短于 30 字的 0 段。

- **FR-401** 切段单元：每个标题行到下一个**任意级别**标题行之前的内容是一个单元，标题行计入该单元的 `text`。第一个标题之前的内容是一个单元，章节路径为空。单元末尾的分隔线（FR-301）不计入单元；只剩分隔线的单元丢弃。
- **FR-401a** 标题单元前并：单元长度 < `min_chars`、不含表格，且紧随其后的单元是它的子章节（后者的 `title_path` 以前者为前缀且更长）时，把它并到后一单元开头；合并后的单元取后一单元的 `title_path`，`text` 是两者在原文中的连续切片。从前往后执行，可以连续前并（例如一级、二级标题都几乎没有内容时，一起并入第一个三级标题），判断长度时计入已前并的内容。
- **FR-402** 单元长度 ≤ `max_chars` 且不含表格：整体成为一段。
- **FR-403** 其他单元按块顺序处理。块 = 标题行、段落（空行分隔）、列表、代码块、分隔线、表格。表格按 FR-405 处理，其余连续块贪心装箱：段 `text` 取原文中从首块起点到末块终点的连续切片（含块间换行），长度 ≤ `max_chars`，放不下时开始新段。标题行只出现在单元的第一段，后续段的章节路径不变。当前段长度 < `min_chars` 而下一个块放不进当前段时，按 FR-404 切开该块，第一片的限额为 `max_chars` 减去"当前段起点到该块起点"的长度，使当前段（例如只有标题行）与第一片合成一段。
- **FR-404** 切块：段落和列表在句末标点（`。！？；.!?;` 和换行）之后切开，标点留在前一片，各片贪心合并到不超过限额；单句仍超长则硬切。代码块 ≤ `2 × max_chars` 时不在内部切，可以单独成段（此时可超过 `max_chars`）；当前段长度 < `min_chars` 时，当前段可与这样的整块代码合成一段，合计不超过 `2 × max_chars`。代码块 > `2 × max_chars` 时按行装箱（单行超长则硬切）。
- **FR-405** 表格：
  1. 表格标题：表格前紧邻的块是段落且长度 ≤ `min_chars`（例如 `**status 状态**：`）时，它是该表格的标题，不参与普通装箱。
  2. 表格段 = [表格标题 + 两个换行] + 表头行 + 分隔行 `| --- |` + 若干数据行。按数据行贪心装箱，每段不超过 `max_chars`；只含一条数据行仍超长时，该段单独超长，不再切。表格标题和表头在每个表格段开头重复。表格行以 Markdown 管道表格格式写入：单元格内的 `|` 转义为 `\|`，单元格内换行替换为空格。
  3. 前导短段：单元内表格之前的内容装箱后，若最后一段长度 < `min_chars`（例如只有 `### 枚举值` 标题行），把它加两个换行放到第一个表格段最前面；如果这样连第一条数据行都放不下，则保持独立段，交给 FR-406。
  4. 位置：第一个表格段覆盖前导短段、表格标题到该段最后一条数据行；后续表格段只覆盖自己的数据行，重复的标题和表头不计入。
- **FR-406** 短段合并：切完后从前往后检查所有段（含表格段）。长度 < `min_chars` 的段按以下顺序并入第一个满足条件的相邻段：
  1. 前一段，且 `title_path` 相同（接在其末尾）；
  2. 后一段，且其 `title_path` 以本段的 `title_path` 为前缀（同一章节或其子章节，放在其开头）；
  3. 前一段，且父章节路径（`title_path` 去掉最后一级）相同；
  4. 后一段，且父章节路径相同。

  每条都要求：两段长度之和加 2 ≤ `max_chars`；两段 `location` 的类型相同，`sheet` 类型还要求同一工作表（否则合并后的位置无法引用）。两段用两个换行连接，合并后 `title_path` 取被并入段的，`location` 取并集。并入后一段后，后一段轮到检查时按合并后的长度判断。都不满足时保持原样。
- **FR-407** 每段字段：`chunk_id`、`doc_id`、`scope`、`title_path`、`text`（原文）、`index_text`（用于建索引，见下）、`location`（见第 8.2 节）、`ordinal`（文档内序号，从 0 开始）。
- **FR-408** `index_text` = 文档标题 + 换行 + 章节路径用 ` > ` 连接 + 换行 + `text`。
- **FR-409** `chunk_id` = `sha1(doc_id + "\n" + " > ".join(title_path) + "\n" + text + "\n" + n)` 的前 16 位十六进制，`n` 是同一文档内此前出现过的完全相同（`title_path` 和 `text` 都相同）段的个数，从 0 开始。文档其他位置的修改不改变未变段的 `chunk_id`；段内容变化后 `chunk_id` 随之变化。

---

## 7. 索引与检索

### 7.1 关键词索引（必选）

- **FR-501** 分词器 `cjk_bigram`（默认）：
  1. 文本做 Unicode NFKC 归一，转小写。
  2. 连续的汉字（码点范围 U+3400–U+4DBF、U+4E00–U+9FFF、U+F900–U+FAFF）：输出每个单字，以及每对相邻两字。
  3. 连续的 `[a-z0-9_.\-]`：去掉首尾的 `.` 和 `-` 后输出整串；若含 `_`、`.`、`-`，再按这些符号拆开输出各部分；整串和各部分都至少 1 个字符才输出。
  4. 其他字符（标点、空白、表情等）丢弃。不去停用词。
- **FR-502** 可选分词器 `jieba`（需用户自行安装 jieba，未安装时选它报错）：jieba 精确模式分词，英文和标识符处理同 `cjk_bigram` 第 3 步。
- **FR-503** 排序函数 BM25，默认 `k1 = 1.2`，`b = 0.5`（调参依据见第 11.2 节）：
  - 词频 `tf = tf_body + title_weight × tf_meta`。`tf_body` 是段 `text` 中的词频；`tf_meta` 是文档标题、`aliases` 和章节路径中的词频；两者分别对各自的文本分词，不重复计数（`text` 中的标题行算 `tf_body`）。`title_weight` 默认 2。
  - 段长 `dl` = 该段所有词的加权词频之和；`avgdl` 为全库平均。
  - `idf = ln(1 + (N − n + 0.5) / (n + 0.5))`，N 为段总数，n 为含该词的段数。
  - 查询词去重，每个词计一次。段分数 = Σ `idf × tf × (k1 + 1) / (tf + k1 × (1 − b + b × dl / avgdl))`。
  - 关键词召回只返回分数 > 0 的段。
- **FR-504** 查询分词与文档分词使用同一分词器。关键词召回分词前，先在 NFKC 归一并转小写后的查询中把疑问词替换为空格，疑问词表依次为：`为什么`、`怎么`、`怎样`、`如何`、`什么`、`哪些`、`哪个`、`是否`、`多少`（按此顺序替换）。替换后分词为空时（查询只有疑问词），用原查询分词。向量召回、`identifiers`、`missing_identifiers`、同义词匹配都用原始查询。理由：疑问词不表达主题，却会和索引类文档里"怎么看""有哪些"这类行匹配，把这类文档排到第一；实测去掉后 Q-02 第一条相关从 75.6% 提到 86.7%，Q-01 不变（第 11.2 节）。

### 7.2 向量索引（默认开启）

- **FR-511** 默认模型：`BAAI/bge-small-zh-v1.5`（MIT，HF revision `7999e1d3359715c523056ef9478215996d62a620`），导出为 ONNX 使用。取 CLS 向量后 L2 归一化，512 维。
- **FR-512** 模型分发：仓库提供导出脚本 `tools/export_embedder.py`（导出时才需要 torch 和 transformers，用 `uv run --with` 临时安装，不进项目依赖）。导出规格：fp32、不量化、opset 17，输入 `input_ids`、`attention_mask`、`token_type_ids`（int64，batch 和序列长度为动态维度），输出 `embedding`（float32，`[batch, 512]`，即 `last_hidden_state[:, 0]` 做 L2 归一化）；`tokenizer.json` 直接取 HF 仓库同一 revision 的文件。导出必须用 TorchScript 导出器（`dynamo=False`），导出后脚本校验：opset 为 17、`model.onnx` 是单文件（不引用外部权重数据、目录中没有 `model.onnx.*` 附属文件）、onnxruntime 能加载、输入名为上述三个、输出名为 `embedding` 且 batch 为 1 和 3 时形状为 `[batch, 512]`，任一项不满足报错退出；通过后打印两个文件的 SHA-256 和大小。维护者执行导出并上传到本仓库 GitHub Release（标签 `models-v1`），再把下载地址和两个文件的 SHA-256 写进代码常量；下载后校验，不一致报错。Release 发布前，开发和测试用 `models_dir` 指向本地导出的文件。
- **FR-513** 模型存放：`~/.cache/wensuo/models/<模型名>/`，可用配置 `models_dir` 指定。`ws models download` 预先下载；查询时发现缺模型，报错并提示执行该命令，不自动联网（避免离线环境卡住）。
- **FR-514** 文档侧编码 `index_text`（不加前缀）。内容 token 数（不含 `[CLS]`、`[SEP]`）≤ 510 时整段编码；超过时按每窗 448 个内容 token、步长 384 切窗（最后一窗不足 448 照用），每窗前后加 `[CLS]`、`[SEP]` 分别编码，各窗向量算术平均后再 L2 归一化。窗口起点为 0、384、768……，某一窗覆盖到最后一个 token 即停止，不再产生完全落在前一窗之内的窗口。
- **FR-515** 查询侧在查询前加指令前缀 `为这个句子生成表示以用于检索相关文章：`，内容截断到 510 token。查询最长 1,000 个码点，超出报参数错误。
- **FR-516** 相似度用点积，暴力计算全部段向量。向量以 float16 存储，计算时转 float32。
- **FR-517** 配置 `dense.enabled: false` 时完全不加载模型，检索只走关键词，`sources` 中没有 `dense`。`dense.model` 只接受 `bge-small-zh-v1.5`，其他值报 `ConfigError`。

### 7.3 召回与融合

检索流水线**严格按以下顺序**执行，未开启重排时跳过第 5、7 步：

1. 过滤（第 7.6 节）确定候选段集合。
2. 关键词、向量两路召回。
3. 加权 RRF 融合。
4. 标识符分层，得到融合名次 r（从 1 开始）。
5. 取前 `rerank.top_k` 段重排，其余段丢弃。
6. 排序：先按层，层内按 `final`（重排）或 `fused`（未重排）降序，同分按 `chunk_id` 升序。
7. 丢弃 `p < min_score` 的段。
8. 每篇文档只保留排序最靠前的 `max_per_doc` 段；开启章节扩展（FR-605）时，同一 `(doc_id, title_path)` 只保留第一段。
9. 截取前 `top_k` 段，`rank` 从 1 开始编号。
10. 开启章节扩展时，按 FR-605 替换每条结果的 `text` 和 `location`。


- **FR-601** 召回：关键词、向量两路各取前 `candidates`（默认 50）段。过滤条件（第 7.6 节）在召回前生效。
- **FR-602** 融合用加权 RRF 加关键词头名加分：`fused = Σ w_i / (20 + rank_i) + bonus`，`rank_i` 从 1 开始，未被某路召回的段该路不计分。`bonus` 只看关键词一路的名次（`w_lexical > 0` 时）：第 1 名 0.01，第 2~3 名 0.005，其余 0。默认 `w_lexical = 1.0`，`w_dense = 0.25`。头名加分借鉴 qmd：纯 RRF 下，两路都排得中等的段会压过关键词第 1 名的精确命中，加分保护精确命中；只加给关键词一路，是因为实测向量一路单独排序明显更弱（第 11.2 节）。
- **FR-603** 标识符精确命中分层：提取查询中的全部标识符（定义见第 4 节，比较时小写）。若查询含标识符，按"段的 `index_text` 中字面出现的不同标识符个数"降序分层，层内按 `fused` 降序。查询不含标识符时不分层。
- **FR-604** 每篇文档最多返回 `max_per_doc`（默认 3）段（上述第 8 步）。
- **FR-605** 章节扩展（借鉴 haiku.rag，默认关闭；命令行 `--expand-section`，API `SearchOptions(expand_section=True)`）：结果的 `text` 换成同一文档内与命中段 `title_path` 相同、`ordinal` 连续的全部段按顺序用两个换行连接的文本，`location` 取这些段的并集，`expanded` 为 `true`；`max_chars` 截断作用于扩展后的文本。排序、分数和 `chunk_id` 不变（仍是命中段）。

### 7.4 重排（可选，默认关闭）

- **FR-611** 配置 `rerank.model_dir` 指向一个模型目录时开启。目录内必须有：
  - `model.onnx`：交叉编码器，输入 `input_ids`、`attention_mask`，可选 `token_type_ids`；输出 logits，形状 `[batch, 1]` 或 `[batch, 2]`。
  - `tokenizer.json`
  - `model_meta.json`：`{"max_length": 384, "query_max_tokens": 64, "passage_chars": 320, "temperature": 1.0, "bias": 0.0, "default_min_score": 0.2}`，其中 `max_length`、`temperature`、`bias`、`default_min_score` 必填。
- **FR-612** 输入：查询截断到 `query_max_tokens`；段落 = 章节路径（` > ` 连接）+ 换行 + `text` 前 `passage_chars` 个字符；按 `[CLS] 查询 [SEP] 段落 [SEP]` 编码，总长截断到 `max_length`。
- **FR-613** 概率：logit = 输出为 1 列时取该列，为 2 列时取 `第 2 列 − 第 1 列`；`p = sigmoid(logit / temperature + bias)`。
- **FR-614** 对融合排序后的前 `rerank.top_k`（默认 30）段重排。按融合名次 r 计算 `s_ret = 1 − (r − 1) / top_k`，`final = w × s_ret + (1 − w) × p`，其中 r ≤ 3 时 w = 0.75，4 ≤ r ≤ 10 时 w = 0.60，r > 10 时 w = 0.40。r 是第 4 步得到的分层后名次。排序见第 7.3 节第 6 步。
- **FR-615** 阈值：`p < min_score` 的段丢弃。`min_score` 默认取 `model_meta.json` 的 `default_min_score`，可由配置或参数覆盖。

### 7.5 分数与"查不到"

- **FR-621** 每条结果的 `score` 和 `score_kind`：
  - 开启重排时 `score = p`，`score_kind = "calibrated"`（可作阈值）。此时结果顺序以 `rank` 为准，`score` 不保证单调。
  - 未开启重排时 `score = fused`，`score_kind = "rank_only"`（只能比较先后，不能作阈值）。
- **FR-622** 未开启重排时传入 `min_score`，报参数错误。
- **FR-623** 查询结果附带 `identifiers`（查询中的标识符列表）和 `missing_identifiers`（在整个知识库所有段的 `index_text` 中都没有"标识符命中"（第 4 节，子串加边界判断）的标识符；`_`、`%` 等按字面比较）。
- **FR-624** `search` 执行成功且满足以下任一情况时记入缺口清单（索引目录内）：结果为空；或 `missing_identifiers` 非空。参数错误、运行错误、`ws eval` 发起的查询不记录。去重键为（查询原文，过滤条件的规范化 JSON（键排序、去掉值为空的键），调用入口 cli/api）。记录字段：`query`、`filters`、`entry`、`count`（累计次数）、`first_seen`、`last_seen`（`YYYY-MM-DD HH:MM:SS`）、`last_result_count`、`missing_identifiers`。`ws gaps` 按 `count` 降序、同值按 `last_seen` 降序列出。
- **FR-625** 查询分词后没有任何词（例如全是标点）时，报参数错误。

### 7.6 过滤与同义词

- **FR-631** 过滤条件（可组合，全部为"与"关系）：
  - `scope`：可多个，"或"关系，精确匹配（NFC）。
  - `path_prefix`：`doc_id` 的字符串前缀匹配（NFC 后逐码点比较，区分大小写，`_`、`%` 按字面），不做目录边界处理（`a/b` 匹配 `a/bc/x.md`，`docs` 不匹配 `Docs/x.md`）。
  - `tags`：可多个，文档须包含全部，区分大小写（NFC）。
  - `file_types`：扩展名列表，带不带点都接受，内部统一为小写无点。
  - `modified_after`：`YYYY-MM-DD`，文件修改时间 ≥ 本地时区该日 00:00。
- **FR-632** 同义词：`<root>/synonyms.yaml`（可选），格式为词组列表，例如 `- [登录, 登陆, sign in]`。对 NFKC 归一并转小写后的原始查询做子串匹配，命中组内任一词时，把组内其他词分词后并入关键词查询的词集（与原词同权，去重）。向量召回、`identifiers`、`missing_identifiers` 只用原始查询。同义词文件变化不需要重建索引。

---

## 8. 接口

### 8.1 命令行

```text
ws [--root PATH | --kb NAME] <命令> [选项]

ws search QUERY [--top-k 5] [--scope S]... [--path-prefix P] [--tag T]... [--file-type EXT]...
                [--modified-after YYYY-MM-DD] [--min-score F] [--max-chars 800] [--expand-section]
                [--format text|json] [--no-sync] [--threads N]
ws read DOC_ID [--section "标题 > 子标题"] [--offset 0] [--max-chars 20000] [--format text|json] [--no-sync]
ws show CHUNK_ID [--format text|json]
ws docs [--scope S]... [--path-prefix P] [--format text|json] [--no-sync]
ws scopes [--format text|json]
ws sync [--threads N] [--format text|json]
ws doctor [--format text|json]
ws gaps [--limit 50] [--format text|json] [--clear]      # --limit 必须是正整数，否则参数错误
ws eval QUERIES.jsonl [--modes lexical,dense,fused,rerank] [--k 5] [--format text|json]
ws models download [--force]
ws --version
```

- **FR-1001** 退出码：0 = 成功；1 = 仅用于 `search` 无结果、`read`/`show` 找不到对象（其他命令结果为空也返回 0）；2 = 参数或配置错误；3 = 运行错误（缺模型、缺扩展、索引损坏、锁超时、模型下载或校验失败等）。`ws doctor` 发现解析失败文件仍返回 0。错误信息写标准错误输出；`--format json` 时错误也以 `{"schema_version": 1, "error": {"code": 2, "message": "…"}}` 写到标准输出。
- **FR-1002** `--format text`（默认）面向人：每条结果一块，块间空一行。第一行 `[rank] score doc_id 位置`，score 保留 4 位小数；位置写法：`lines` → `L12-40`，`page` → `p3-5`，`slide` → `s2-2`，`sheet` → `Sheet1!r5-30`，`section` 不写。第二行章节路径（` > ` 连接，为空时省略该行）。之后是正文，超过 `--max-chars` 截断并以 `…` 结尾。`missing_identifiers` 非空时在最前面输出一行 `知识库中未出现：a, b`。
- **FR-1003** `--format json` 面向程序：输出单个 JSON 对象到标准输出，结构见第 8.2 节；标准输出不混入任何其他内容。标准输出和标准错误输出一律用 UTF-8 编码，不随终端代码页变化（Windows 的 cp1252、GBK 等也一样）。
- **FR-1004** `ws doctor` 输出：根目录、索引目录、配置来源、文档数、段数、各专题文档数、各扩展名文件数、跳过文件（含原因）、解析失败文件（含原因）、向量模型身份和是否就绪、重排模型身份和是否就绪、索引大小。

### 8.2 JSON 结构

`search` 输出：

```json
{
  "schema_version": 1,
  "query": "用户表的 user_id 字段",
  "stale": false,
  "score_kind": "rank_only",
  "identifiers": ["user_id"],
  "missing_identifiers": [],
  "results": [
    {
      "rank": 1,
      "chunk_id": "3f2a9c0d1e4b5a67",
      "doc_id": "数据字典/用户.md",
      "doc_title": "用户表",
      "scope": "数据字典",
      "title_path": ["用户表", "字段说明"],
      "location": {"kind": "lines", "start": 12, "end": 40},
      "score": 0.0312,
      "sources": {"lexical": 1, "dense": 4},
      "text": "……",
      "truncated": false,
      "expanded": false
    }
  ]
}
```

- `location` 只覆盖段内容在原文中的连续区间，重复插入的表头行不计入。`location.kind` 取值：`lines`（Markdown、TXT、CSV/TSV：`start`、`end` 为 1 起始的行号，含标题行，闭区间）、`page`（PDF：`start`、`end` 页码）、`slide`（PPTX：`start`、`end`）、`sheet`（XLSX：`sheet`、`row_start`、`row_end`，表头固定为第 1 行，`row_start` 是该段第一条数据行的行号）、`section`（DOCX、HTML：无数字位置，只有章节路径）。
- `sources` 中某路未召回该段时，该键不出现；开启重排时增加 `"rerank": p`。
- `read` 输出：`schema_version`、`doc_id`、`doc_title`、`scope`、`tags`、`text`、`offset`、`next_offset`（没有更多内容时为 `null`）。
- `show` 输出：`schema_version` 加单段的全部字段：`results` 元素中除 `rank` 以外的字段（没有查询，`score` 为 `null`、`sources` 为 `{}`，`truncated` 与 `expanded` 为 `false`，不截断），另加 `ordinal`。
- `docs` 输出：`schema_version` 和 `docs` 列表，每项含 `doc_id`、`doc_title`、`scope`、`tags`、`chunks`、`modified`（`YYYY-MM-DD HH:MM`），按 `doc_id` 升序。
- `scopes` 输出：`schema_version` 和 `scopes` 列表，每项含 `scope`、`description`（无则 `null`）、`docs`（文档数），按 `scope` 升序。
- `sync` 输出：`schema_version`、`added`、`modified`、`deleted`、`skipped`、`failed`（均为 doc_id 列表）、`chunks_total`、`seconds`。
- `doctor` 输出：`schema_version`、`root`、`index_dir`、`config_files`（列表）、`docs`、`chunks`、`scopes`（专题 → 文档数）、`file_types`（扩展名 → 文件数）、`skipped`（`[{doc_id, reason}]`）、`failed`（`[{doc_id, reason}]`）、`dense`（`{enabled, model, ready}`）、`rerank`（`{enabled, model_dir, ready}`）、`index_bytes`。
- `gaps` 输出：`schema_version` 和 `gaps` 列表，字段见 FR-624。
- `eval` 输出：`schema_version`、`k`、`modes`（模式 → `{hit_at_k, top1, mrr_at_k, answerable_return_rate, false_return_rate, n}`，数据中没有对应查询的指标为 `null`）。
- JSON 字段只增不改；改动已有字段含义时 `schema_version` 加 1。

### 8.3 Python API

```python
from axc_wensuo import KnowledgeBase

kb = KnowledgeBase('/path/to/docs')          # 或 KnowledgeBase.open('notes') 按全局配置名字打开
report = kb.sync()                            # SyncReport
res = kb.search('用户表的 user_id 字段', top_k=5, scope=['数据字典'])  # SearchResult
doc = kb.read('数据字典/用户.md', section='用户表 > 字段说明')
chunk = kb.show('3f2a9c0d1e4b5a67')
docs = kb.list_docs(scope=['数据字典'])
kb.close()                                    # 也支持 with 语句
```

- **FR-1021** 返回对象是 dataclass，`to_dict()` 输出与第 8.2 节 JSON 完全相同。
- **FR-1022** 同一 `KnowledgeBase` 实例只加载一次模型；线程安全不做要求（文档注明"一个线程一个实例"）。
- **FR-1023** 异常类型：`ConfigError`（对应退出码 2）、`WenSuoRuntimeError`（对应退出码 3），都继承 `WenSuoError`。

---

## 9. 配置（`wensuo.yaml`）

全部字段可选，以下为默认值：

```yaml
name: null                  # 知识库显示名，默认为根目录名
index_dir: null             # 默认 <root>/.wensuo/
models_dir: null            # 默认 ~/.cache/wensuo/models/
threads: null               # 默认 max(1, CPU 核数 // 2)
include: []                 # 空 = 收纳所有支持的扩展名
exclude: []
max_file_mb: 50
scopes: {}                  # 专题描述，例如 {数据字典: "表结构与字段说明"}
scope_rules: []             # 例如 [{glob: "**/*报销*", scope: 财务}]
chunk: {max_chars: 1200, min_chars: 80}
lexical: {tokenizer: cjk_bigram, title_weight: 2, k1: 1.2, b: 0.5}
dense: {enabled: true, model: bge-small-zh-v1.5}
fusion: {w_lexical: 1.0, w_dense: 0.25, candidates: 50, max_per_doc: 3}
rerank: {model_dir: null, top_k: 30, min_score: null}
```

- **FR-1101** 命令行参数优先于 `wensuo.yaml`，`wensuo.yaml` 优先于默认值。
- **FR-1102** 类型错误、取值越界、未知字段都报 `ConfigError`，错误信息包含字段路径。取值约束：`max_chars > min_chars ≥ 1`；`title_weight`、`w_lexical`、`w_dense` ≥ 0 且 `w_lexical + w_dense > 0`；`candidates`、`max_per_doc`、`rerank.top_k`、`threads`、`max_file_mb` 为正整数；`k1 > 0`；`0 ≤ b ≤ 1`；`min_score` 在 [0, 1]；所有数字必须是有限值（NaN、Infinity 报错）；`scope_rules` 每项必须且只能有非空字符串 `glob` 和 `scope`。

---

## 10. 非功能需求

基准环境：Apple M 系列或同级 x86，`threads = 1`（N-06 除外），语料为 2,500 段中文 Markdown（约 150 万字），已建好索引。测量方法：仓库提供 `tools/bench.py`，用固定随机种子生成语料和 200 条查询（一半中文短语、一半含标识符）；延迟类指标在同一进程内预热 10 次后采样 200 次取 p95；N-03、N-05 各测 10 次取最大值；N-04 用生成的 1 万个小文件。

| 编号 | 项 | 要求 |
| --- | --- | --- |
| N-01 | 查询延迟（不重排） | 不含进程启动，p95 ≤ 200 ms |
| N-02 | 查询延迟（重排 30 段，13 MB 级模型） | p95 ≤ 1 s |
| N-03 | 命令行冷启动 | 新进程从执行 `ws search` 到退出 ≤ 2 s（含 Python 启动、加载向量模型、无变化同步） |
| N-04 | 无变化时的同步开销 | 1 万个文件 ≤ 300 ms |
| N-05 | 改 1 个 Markdown 文件（约 5 段）后的同步 | 同一进程内，模型已加载时 ≤ 3 s |
| N-06 | 首次建索引 | 2,500 段 ≤ 5 分钟（4 线程） |
| N-07 | 内存 | 查询进程常驻 ≤ 1 GB（10 万段规模） |
| N-08 | 安装体积 | 核心包 + 默认向量模型 ≤ 300 MB；运行时不安装 torch |
| N-09 | 平台 | macOS、Linux、Windows；Python 3.11 ~ 3.13 |
| N-10 | 离线 | 模型下载后全程不访问网络 |

---

## 11. 验收标准

### 11.1 功能验收（公开、可复现）

开发者在 `tests/fixtures/corpus/` 下自行构造一个公开的小语料（内容虚构、不含任何真实组织信息），至少包含：多级标题的 Markdown、带文件头 YAML 的 Markdown、含长表格（≥ 100 行）的 Markdown、含代码块的 Markdown、TXT（GB18030 编码一份）、CSV、PDF（≥ 3 页且含表格）、DOCX、PPTX、XLSX（≥ 2 个工作表）。以下每条都必须有自动化测试：

| 编号 | 验收内容 |
| --- | --- |
| A-01 | 安装核心包后，`ws search` 在无网络、无常驻进程的情况下可用 |
| A-03 | 新增文件后直接 `ws search`，能检索到新文件内容 |
| A-04 | 修改文件后直接 `ws search`，旧内容检索不到、新内容检索得到；只有该文件被重新解析 |
| A-05 | 删除文件后直接 `ws search`，该文件的段不再出现 |
| A-06 | 删除索引目录后再查询，结果与删除前逐条一致 |
| A-07 | 中文查询命中：查询中的两字词在段中出现即可召回 |
| A-08 | 标识符查询：`user_id` 能命中含 `user_id` 的段，`user` 也能命中（拆分） |
| A-09 | 长表格第 90 行内容的查询，结果段含表头行 |
| A-10 | 专题规则四级优先顺序（文件头 > `scope_rules` > 子目录 > `_root`）各有测试 |
| A-11 | `scope`、`path_prefix`、`tags`、`file_types`、`modified_after` 过滤各有测试 |
| A-12 | Markdown 结果的行号区间包含命中文本 |
| A-13 | PDF 结果的页码正确；XLSX 结果的工作表名和行号正确 |
| A-14 | 章节路径与原文标题层级一致 |
| A-15 | 同一 `search` 查询，命令行 JSON、Python API `to_dict()` 输出完全相同（`stale` 除外） |
| A-16 | 退出码 0/1/2/3 四种情况各有测试 |
| A-17 | `--format json` 的标准输出能被 JSON 解析，且符合第 8.2 节结构 |
| A-18 | 查询含知识库中不存在的标识符时，`missing_identifiers` 列出它，并记入缺口清单 |
| A-19 | 测试中用 `onnx` 库（仅测试依赖）在运行时生成按输入输出固定 logits 的小模型，放入临时 `model_dir`，不为测试增加生产代码接口。验证：低于 `min_score` 的段被丢弃，全部低于时结果为空、退出码 1 |
| A-20 | 未开启重排时传 `--min-score`，退出码 2 |
| A-21 | 第 7.4 节按名次加权公式与标识符分层，用假重排模型验证排序 |
| A-22 | 未安装 `docs` 扩展时，PDF 等文件出现在 `ws doctor` 的跳过列表，其余文件正常检索 |
| A-23 | 解析失败的文件出现在 `ws doctor`，且文件不变时不重试 |
| A-24 | 两个进程同时同步，不损坏索引；一个同步时另一个查询返回 `stale: true` |
| A-25 | 同一输入重复查询 100 次，结果顺序完全一致 |

### 11.2 检索质量验收（维护者本地执行）

维护者持有基于自有文档库构造的私有评测集（放在 `eval/private/`，不入库）。开发者只需保证 `ws eval` 能读取第 11.3 节格式并输出指标；维护者用默认配置（无重排）执行，必须达到：

| 编号 | 数据 | 指标 | 门槛 |
| --- | --- | --- | --- |
| Q-01 | agent 真实查询，32 条（每条自带 `k`） | 前 k 条有相关 / 第一条相关 | ≥ 96% / ≥ 84% |
| Q-02 | 人工问题，45 条 | 前 5 条有相关 | 不低于同一数据上纯关键词模式（`--modes lexical`）的结果 |
| Q-03 | 无答案查询，171 条（配置维护者的重排模型） | 误返回率 / 可答问题返回率 | ≤ 2% / ≥ 90% |

Q-01、Q-02 不达标时，允许调整第 7.1 节、第 7.3 节的默认参数；不允许为通过评测而修改评测数据。开发者只负责 `ws eval` 指标计算正确（用 11.1 的公开语料构造小评测集测试），不对 Q-* 数值负责；Q-* 由维护者执行并决定是否通过。

**v1.1 调参记录**（切段版本 2；Q-01 由大模型按 0~3 分打分，≥ 2 记为相关；Q-02 按文档匹配）：

| 配置 | Q-01 前 k / 第一条 | Q-02 前 5 / 第一条 |
| --- | --- | --- |
| v1.0 默认（切段 1，`b=0.75`，RRF 60，`w_dense=0.5`） | 84.4% / 75.0% | 91.1% / 82.2% |
| 切段 2，参数仍为 v1.0 默认 | 93.8% / 78.1% | 91.1% / 80.0% |
| 切段 2，纯关键词（`b=0.5`） | 96.9% / 84.4% | 97.8% / 73.3% |
| 切段 2，`b=0.5`，RRF 20，`w_dense=0.25`，头名加分 | 96.9% / 87.5% | 97.8% / 75.6% |
| 上一行 + 维护者重排模型（`min_score=0`） | 96.9% / 87.5% | 95.6% / 75.6% |
| **v1.1 默认 = 上上行 + 查询去疑问词（FR-504）** | **96.9% / 87.5%** | **97.8% / 86.7%** |
| v1.1 默认，纯关键词 | 96.9% / 84.4% | 97.8% / 88.9% |

`ods_member 邮箱 email` 一条在知识库中没有答案，计入分母。以召回（前 k 条有相关）为首要目标选参：BM25 `k1`∈{0.9,1.2,1.6}、`b`∈{0.3,0.5,0.75}，RRF 常数∈{20,60}，`w_dense`∈{0.1,0.15,0.25,0.35,0.5,0.75,1.0}，头名加分∈{无, 0.01/0.005, 0.02/0.01, 0.05/0.02}×{只加关键词, 两路都加}。v1.1 默认是两套评测召回都最高的组合中第一条相关最高的一组；加入 FR-504 后重扫 RRF 常数与 `w_dense`，默认组合仍在最优之列（`w_dense=0.1` 时 Q-02 第一条相关多 1 条、Q-01 第一条相关少 1 条），默认值不变。不加 FR-504 时，切段 2 下 Q-02 第一条相关最高只有 80.0%：版本 1 的 82.2% 部分依赖只有标题行的碎片段（单走向量时第一条相关 75.6%，版本 2 为 71.1%），而这类碎片正是本版要消除的。

### 11.3 评测集格式（`ws eval`）

jsonl，每行一个对象：

```json
{"id": "q1", "query": "……", "relevant": [{"doc_id": "a/b.md", "title_path": ["标题"]}], "k": 5}
{"id": "q2", "query": "……", "no_answer": true}
```

- 结果段的 `doc_id` 相同，且其 `title_path` 以 `relevant` 中给出的 `title_path` 为前缀（未给 `title_path` 时只比 `doc_id`），即视为相关。
- `k` 省略时用命令行 `--k`。
- 输出指标（每种模式）：
  - `hit_at_k`：有 `relevant` 的查询中，前 k 条至少一条相关的比例。
  - `top1`：有 `relevant` 的查询中，第一条相关的比例。
  - `mrr_at_k`：第一条相关结果名次倒数的平均（前 k 条没有相关记 0）。
  - `answerable_return_rate`：有 `relevant` 的查询中，返回至少一条结果的比例。
  - `false_return_rate`：`no_answer` 查询中，返回至少一条结果的比例。
- 模式含义：`lexical` 只用关键词召回；`dense` 只用向量召回；`fused` 为默认流水线不重排；`rerank` 为默认流水线加重排（未配置重排模型时选它报参数错误）。各模式都执行标识符分层和 `max_per_doc`。

---

## 12. 代码结构

```text
src/axc_wensuo/
  __init__.py        # 导出 KnowledgeBase、异常类、__version__
  errors.py          # WenSuoError、ConfigError、WenSuoRuntimeError
  config.py          # 全局配置与 wensuo.yaml 的加载、校验、合并
  models.py          # Document、Chunk、Location、SearchHit、SearchResult、SyncReport 等 dataclass
  source.py          # 目录扫描、收纳规则、专题规则、变化检测
  parsers/           # markdown.py、text.py、table.py、docling.py；按扩展名注册
  chunker.py         # 第 6.3 节切段
  tokenizers.py      # cjk_bigram、jieba
  lexical.py         # BM25 倒排索引
  dense.py           # ONNX 向量模型与向量存储
  rerank.py          # ONNX 交叉编码器与校准
  search.py          # 召回、融合、分层、重排、过滤、阈值
  store.py           # SQLite 存储（WAL 模式）与文件锁
  gaps.py            # 缺口清单
  evaluate.py        # ws eval
  kb.py              # KnowledgeBase 门面
  cli.py             # 命令行
tools/export_embedder.py
tests/
```

- **FR-1201** 存储：索引目录下一个 SQLite 文件 `index.sqlite`（WAL 模式），存文档、段、倒排表、向量（float16 BLOB）、同步状态、解析失败记录、缺口清单；Docling 解析缓存存在 `parsed/` 子目录。整个索引目录可拷贝到另一台机器直接使用（同路径结构下）。
- **FR-1202** 代码风格：Tab 缩进，行宽 120，ruff 检查通过；测试用 pytest；包管理用 uv。

---

## 13. 风险与已定对策

| 风险 | 对策（已定，开发时照此执行） |
| --- | --- |
| 通用重排模型缺失：现有小模型跨领域会变差，通用大模型单核太慢 | 重排默认关闭，作为用户自带的可选组件；默认配置只靠关键词 + 向量 + 标识符分层达到 Q-01、Q-02 |
| 向量召回在 agent 查询上偏弱，简单融合会压低关键词精确命中 | `w_dense = 0.25`、关键词头名加分、标识符分层（FR-602、FR-603）；按 Q-01、Q-02 结果调参 |
| Docling 依赖重（需要 torch 与版面模型） | 放在 `docs` 扩展；核心安装不带它；未安装时跳过相应文件并在 `doctor` 中提示 |
| SQLite FTS5 默认不切中文 | 不用 FTS5，倒排表自实现（FR-501、FR-503） |
| 首次给大量 PDF 建索引很慢 | 同步显示进度（FR-208）；`--threads` 可调；首次同步期间其他进程查询返回 `stale: true`（FR-207） |
| 许可证 | 本项目 Apache-2.0；依赖 Docling（MIT）、bge-small-zh-v1.5（MIT）、onnxruntime（MIT）、tokenizers（Apache-2.0）、filelock（Unlicense）、PyYAML（MIT） |

---

## 附：维护者已有的原型

维护者有一个约 800 行的原型，已实现切段、增量同步、`cjk_bigram` 分词、RRF 融合和评测脚本，第 1.3 节的数据由它测得。原型不随仓库发布；本文档已覆盖其全部行为，开发以本文档为准。
