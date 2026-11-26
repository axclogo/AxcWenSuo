"""AxcWenSuo 文索：中文优先、不起服务、目录即知识库的本地 RAG 引擎。"""

from __future__ import annotations

from axc_wensuo.errors import ConfigError, WenSuoError, WenSuoRuntimeError
from axc_wensuo.kb import KnowledgeBase

__version__ = '0.1.0'

__all__ = ['ConfigError', 'KnowledgeBase', 'WenSuoError', 'WenSuoRuntimeError', '__version__']
