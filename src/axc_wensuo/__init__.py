"""AxcWenSuo: offline, Chinese-first RAG engine. 文索：中文优先、离线本地 RAG 引擎。"""

from __future__ import annotations

from axc_wensuo.errors import ConfigError, WenSuoError, WenSuoRuntimeError
from axc_wensuo.kb import KnowledgeBase, ListDocsOptions, ReadOptions, SearchOptions

__version__ = '0.1.0'

__all__ = [
	'ConfigError',
	'KnowledgeBase',
	'ListDocsOptions',
	'ReadOptions',
	'SearchOptions',
	'WenSuoError',
	'WenSuoRuntimeError',
	'__version__',
]
