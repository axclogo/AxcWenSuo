"""MCP stdio server. MCP stdio 服务。"""

from __future__ import annotations

import json
from typing import Any

from axc_wensuo.errors import WenSuoError, WenSuoRuntimeError
from axc_wensuo.filters import Filters, parse_modified_after
from axc_wensuo.kb import KnowledgeBase, ListDocsOptions, ReadOptions, SearchOptions


def _dumps(data: dict[str, Any]) -> str:
	return json.dumps(data, ensure_ascii=False)

def _error(exc: WenSuoError) -> str:
	return _dumps({'schema_version': 1, 'error': {'code': exc.code, 'message': exc.message}})

def _not_found(message: str) -> str:
	return _dumps({'schema_version': 1, 'error': {'code': 1, 'message': message}})

def search_description(kb: KnowledgeBase) -> str:
	"""Describe search for the MCP client. 生成 search 工具描述。"""
	scopes = kb.list_scopes(sync=False)
	lines = [f'知识库：{kb.config.name}（{kb.root}）']
	if scopes.scopes:
		lines.append('专题：')
		for scope in scopes.scopes:
			description = f'：{scope.description}' if scope.description else ''
			lines.append(f'- {scope.scope}{description}（{scope.docs} 篇）')
	lines.append('score_kind：rank_only 只用于排序；calibrated 是校准概率，可用 min_score 过滤。')
	lines.append('missing_identifiers：这些标识符在知识库里没有出现过，请换说法或说明知识库缺失。')
	return '\n'.join(lines)

def _new_server() -> Any:
	try:
		from mcp.server.mcpserver import MCPServer
	except ImportError as exc:
		raise WenSuoRuntimeError('缺少 mcp 扩展：请安装 mcp（pip install "axc-wensuo[mcp]"）后重试') from exc
	return MCPServer('wensuo')

def build_server(kb: KnowledgeBase) -> Any:
	server = _new_server()
	_register_search(server, kb)
	_register_read(server, kb)
	_register_docs(server, kb)
	_register_scopes(server, kb)
	return server

def _register_search(server: Any, kb: KnowledgeBase) -> None:
	# MCP tool schema fixes these names in FR-1012. MCP 工具参数名由 FR-1012 固定。
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
			filters = Filters(
				scope=tuple(scope or ()),
				path_prefix=path_prefix,
				tags=tuple(tags or ()),
				file_types=tuple(file_types or ()),
				modified_after=parse_modified_after(modified_after) if modified_after else None,
			)
			options = SearchOptions(filters=filters, min_score=min_score, max_chars=max_chars, entry='mcp')
			result = kb.search(query, top_k=top_k, options=options)
		except WenSuoError as exc:
			return _error(exc)
		return _dumps(result.to_dict())

def _register_read(server: Any, kb: KnowledgeBase) -> None:
	@server.tool(name='read', description=f'读取知识库 {kb.config.name} 中的整篇文档或指定章节。')
	async def read(doc_id: str, section: str | None = None, offset: int = 0, max_chars: int = 20000) -> str:
		try:
			result = kb.read(doc_id, section=section, options=ReadOptions(offset=offset, max_chars=max_chars))
		except WenSuoError as exc:
			return _error(exc)
		if result is None:
			return _not_found(f'找不到文档或章节：{doc_id}')
		return _dumps(result.to_dict())

def _register_docs(server: Any, kb: KnowledgeBase) -> None:
	@server.tool(name='list_docs', description=f'列出知识库 {kb.config.name} 的文档。')
	async def list_docs(scope: list[str] | None = None, path_prefix: str | None = None) -> str:
		try:
			filters = Filters(scope=tuple(scope or ()), path_prefix=path_prefix)
			result = kb.list_docs(options=ListDocsOptions(filters=filters))
		except WenSuoError as exc:
			return _error(exc)
		return _dumps(result.to_dict())

def _register_scopes(server: Any, kb: KnowledgeBase) -> None:
	@server.tool(name='list_scopes', description=f'列出知识库 {kb.config.name} 的专题及文档数。')
	async def list_scopes() -> str:
		try:
			result = kb.list_scopes(sync=True)
		except WenSuoError as exc:
			return _error(exc)
		return _dumps(result.to_dict())

def run_server(kb: KnowledgeBase) -> None:
	"""Serve MCP over stdio. 以 stdio 运行 MCP。"""
	build_server(kb).run(transport='stdio')


__all__ = ['build_server', 'run_server', 'search_description']
