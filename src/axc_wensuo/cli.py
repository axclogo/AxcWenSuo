"""命令行入口 ws"""

import argparse

from axc_wensuo import __version__


def build_parser() -> argparse.ArgumentParser:
	"""构建命令行解析器；子命令随里程碑逐步加入"""
	parser = argparse.ArgumentParser(prog='ws', description='AxcWenSuo: offline Chinese-first RAG engine')
	parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
	return parser


def main(argv: list[str] | None = None) -> int:
	"""命令行主函数"""
	parser = build_parser()
	parser.parse_args(argv)
	parser.print_help()
	return 0


if __name__ == '__main__':
	raise SystemExit(main())
