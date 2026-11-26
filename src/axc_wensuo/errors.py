"""异常类型。

命令行退出码与异常类型的对应关系（需求 FR-1001、FR-1023）：

* ``ConfigError``：参数或配置错误，退出码 2。
* ``WenSuoRuntimeError``：运行错误（缺模型、缺扩展、索引损坏、锁超时等），退出码 3。
"""

from __future__ import annotations


class WenSuoError(Exception):
	"""所有文索异常的基类。"""

	code: int = 3

	def __init__(self, message: str) -> None:
		super().__init__(message)
		self.message = message


class ConfigError(WenSuoError):
	"""参数或配置错误，对应命令行退出码 2。"""

	code = 2


class WenSuoRuntimeError(WenSuoError):
	"""运行错误，对应命令行退出码 3。"""

	code = 3
