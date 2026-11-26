# AxcWenSuo 文索

Offline, Chinese-first RAG engine with no server: any folder becomes a searchable knowledge base for humans and AI agents, via CLI, MCP and a Python API.

中文优先、不起服务、目录即知识库的本地 RAG 引擎。人和 agent 共用同一个内核。

> 状态：立项（pre-alpha），尚不可用。需求与里程碑见 [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)。

## 计划能力

- 目录即知识库：放入或修改文件，下次查询前自动增量同步，没有构建步骤。
- 中文关键词检索：单字 + 相邻两字 + 标识符拆分的 BM25，可选向量召回与重排。
- 分数可信：重排分数经过校准，阈值有明确含义；无答案时返回空并记入缺口清单。
- 结构化解析：Markdown/TXT/CSV 内置；PDF、Word、Excel、PPT 通过可选扩展解析，保留标题、表格、页码。
- 多种入口：`ws` 命令行（文本与 JSON）、MCP（stdio）、Python API、终端对话。

## 开发

```bash
uv sync
uv run ws --version
uv run pytest
```

## License

[Apache-2.0](LICENSE)
