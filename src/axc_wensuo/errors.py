"""Exception types and exit codes. 异常类型与退出码。"""

from __future__ import annotations


class WenSuoError(Exception):
	"""Base exception. 异常基类。"""

	code: int = 3

	def __init__(self, message: str) -> None:
		super().__init__(message)
		self.message = message

class ConfigError(WenSuoError):
	"""Configuration error, exit 2. 配置错误，退出码 2。"""

	code = 2

class WenSuoRuntimeError(WenSuoError):
	"""Runtime error, exit 3. 运行错误，退出码 3。"""

	code = 3
