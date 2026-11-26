"""MCP stdio 服务（第 8.2 节）。

工具参数名与命令行一致；返回内容与命令行 ``--format json`` 完全相同。每次调用都自动同步。
MCP 不提供 ``show`` / ``sync`` / ``gaps`` / ``eval`` / ``doctor``。
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from axc_wensuo.errors import WenSuoError
from axc_wensuo.filters import parse_modified_after
from axc_wensuo.kb import KnowledgeBase


def _dumps(data: dict[str, Any]) -> str:
	return json.dumps(data, ensure_ascii=False)


def _error(exc: WenSuoError) -> str:
	return _dumps({'schema_version': 1, 'error': {'code': exc.code, 'message': exc.message}})


def _not_found(message: str) -> str:
	return _dumps({'schema_version': 1, 'error': {'code': 1, 'message': message}})


def search_description(kb: KnowledgeBase) -> str:
	scopes = kb.list_scopes(sync=False)
	lines = [f'知识库：{kb.config.name}（{kb.root}）']
	if scopes.scopes:
		lines.append('专题：')
		for scope in scopes.scopes:
			description = f'：{scope.description}' if scope.description else ''
			lines.append(f'- {scope.scope}{description}（{scope.docs} 篇）')
	lines.append(
		'score_kind：rank_only 表示分数只能比较先后、不能当阈值；calibrated 表示校准概率，可用 min_score 过滤。'
	)
	lines.append('missing_identifiers：这些标识符在知识库里没有出现过，请换说法或说明知识库缺失。')
	return '\n'.join(lines)


def _new_server() -> Any:
	"""兼容 mcp 1.x（FastMCP）与 2.x（MCPServer）。"""
	try:
		from mcp.server.fastmcp import FastMCP
	except ImportError:
		try:
			from mcp.server.mcpserver import MCPServer
		except ImportError as exc:  # pragma: no cover - 依赖缺失路径
			from axc_wensuo.errors import WenSuoRuntimeError

			raise WenSuoRuntimeError('缺少 mcp 扩展：请安装 mcp（pip install "axc-wensuo[mcp]"）后重试') from exc
		return MCPServer('wensuo')
	return FastMCP('wensuo')


def build_server(kb: KnowledgeBase) -> Any:
	server = _new_server()

	@server.tool(name='search', description=search_description(kb))
	async def search(
		query: str,
		top_k: int = 5,
		scope: list[str] | None = None,
		path_prefix: str | None = None,
		tags: list[str] | None = None,
		file_types: list[str] | None = None,
		modified_after: str | None = None,
		min_score: float | None = None,
		max_chars: int = 800,
	) -> str:
		try:
			day = parse_modified_after(modified_after) if modified_after else None
			result = kb.search(
				query,
				top_k=top_k,
				scope=scope,
				path_prefix=path_prefix,
				tags=tags,
				file_types=file_types,
				modified_after=day,
				min_score=min_score,
				max_chars=max_chars,
				entry='mcp',
			)
		except WenSuoError as exc:
			return _error(exc)
		return _dumps(result.to_dict())

	@server.tool(name='read', description=f'读取知识库 {kb.config.name} 中的整篇文档或指定章节。')
	async def read(doc_id: str, section: str | None = None, offset: int = 0, max_chars: int = 20000) -> str:
		try:
			result = kb.read(doc_id, section=section, offset=offset, max_chars=max_chars)
		except WenSuoError as exc:
			return _error(exc)
		if result is None:
			return _not_found(f'找不到文档或章节：{doc_id}')
		return _dumps(result.to_dict())

	@server.tool(name='list_docs', description=f'列出知识库 {kb.config.name} 的文档。')
	async def list_docs(scope: list[str] | None = None, path_prefix: str | None = None) -> str:
		try:
			result = kb.list_docs(scope=scope, path_prefix=path_prefix)
		except WenSuoError as exc:
			return _error(exc)
		return _dumps(result.to_dict())

	@server.tool(name='list_scopes', description=f'列出知识库 {kb.config.name} 的专题及文档数。')
	async def list_scopes() -> str:
		try:
			result = kb.list_scopes(sync=True)
		except WenSuoError as exc:
			return _error(exc)
		return _dumps(result.to_dict())

	return server


def run_server(kb: KnowledgeBase) -> None:
	server = build_server(kb)
	parameters = inspect.signature(server.run).parameters
	if 'transport' in parameters:
		server.run(transport='stdio')
	else:  # pragma: no cover - 兼容更老的 mcp 1.x
		server.run()


__all__ = ['build_server', 'run_server', 'search_description']
