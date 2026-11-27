"""ws command line entry. ws 命令行入口。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Any

from axc_wensuo import __version__
from axc_wensuo.config import load_config, resolve_root
from axc_wensuo.dense import download_model
from axc_wensuo.errors import WenSuoError, WenSuoRuntimeError
from axc_wensuo.filters import Filters, parse_modified_after
from axc_wensuo.kb import KnowledgeBase, ListDocsOptions, ReadOptions, SearchOptions
from axc_wensuo.models import ErrorResult, Location

_LAST_PARSE_ERROR: list[str] = []


class _ArgumentParser(argparse.ArgumentParser):
	"""Record parse errors for JSON output. 记录参数错误以便 JSON 输出。"""

	def error(self, message: str) -> None:
		_LAST_PARSE_ERROR.append(message)
		self.print_usage(sys.stderr)
		self.exit(2, f'{self.prog}: error: {message}\n')

def build_parser() -> argparse.ArgumentParser:
	parser = _ArgumentParser(prog='ws', description='AxcWenSuo: offline Chinese-first RAG engine')
	parser.add_argument('--root', help='知识库根目录')
	parser.add_argument('--kb', help='全局配置中登记的知识库名')
	parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
	sub = parser.add_subparsers(dest='command')
	_add_search(sub)
	_add_read(sub)
	_add_show(sub)
	_add_docs(sub)
	_add_scopes(sub)
	_add_sync(sub)
	_add_doctor(sub)
	_add_gaps(sub)
	_add_eval(sub)
	_add_models(sub)
	return parser

def _add_search(sub: argparse._SubParsersAction) -> None:
	parser = sub.add_parser('search', help='检索')
	parser.add_argument('query', help='查询文本')
	parser.add_argument('--top-k', type=int, default=5, help='返回条数（默认 5）')
	parser.add_argument('--scope', action='append', help='按专题过滤，可重复（或关系）')
	parser.add_argument('--path-prefix', help='按 doc_id 字符串前缀过滤')
	parser.add_argument('--tag', action='append', dest='tags', help='按标签过滤，可重复（须全部命中）')
	parser.add_argument('--file-type', action='append', dest='file_types', help='按扩展名过滤')
	parser.add_argument('--modified-after', help='只返回该日期（含）之后修改的文件，YYYY-MM-DD')
	parser.add_argument('--min-score', type=float, help='校准阈值，仅开启重排时可用')
	parser.add_argument('--max-chars', type=int, default=800, help='结果正文最大码点数（默认 800）')
	parser.add_argument('--expand-section', action='store_true', help='结果正文扩展为命中段所在章节的全部段')
	parser.add_argument('--format', choices=('text', 'json'), default='text', help='输出格式')
	parser.add_argument('--no-sync', action='store_true', help='跳过自动同步')
	parser.add_argument('--threads', type=int, help='线程数')

def _add_read(sub: argparse._SubParsersAction) -> None:
	parser = sub.add_parser('read', help='读取文档或章节')
	parser.add_argument('doc_id', help='文档 ID（相对知识库根目录）')
	parser.add_argument('--section', help='章节路径，例如 "父标题 > 子标题"')
	parser.add_argument('--offset', type=int, default=0, help='起始码点偏移（默认 0）')
	parser.add_argument('--max-chars', type=int, default=20000, help='最多返回的码点数')
	parser.add_argument('--format', choices=('text', 'json'), default='text', help='输出格式')
	parser.add_argument('--no-sync', action='store_true', help='跳过自动同步')

def _add_show(sub: argparse._SubParsersAction) -> None:
	parser = sub.add_parser('show', help='显示单个段')
	parser.add_argument('chunk_id', help='段 ID（16 位十六进制）')
	parser.add_argument('--format', choices=('text', 'json'), default='text', help='输出格式')

def _add_docs(sub: argparse._SubParsersAction) -> None:
	parser = sub.add_parser('docs', help='列出文档')
	parser.add_argument('--scope', action='append', help='按专题过滤，可重复')
	parser.add_argument('--path-prefix', help='按 doc_id 前缀过滤')
	parser.add_argument('--format', choices=('text', 'json'), default='text', help='输出格式')
	parser.add_argument('--no-sync', action='store_true', help='跳过自动同步')

def _add_scopes(sub: argparse._SubParsersAction) -> None:
	parser = sub.add_parser('scopes', help='列出专题')
	parser.add_argument('--format', choices=('text', 'json'), default='text', help='输出格式')

def _add_sync(sub: argparse._SubParsersAction) -> None:
	parser = sub.add_parser('sync', help='同步索引')
	parser.add_argument('--threads', type=int, help='线程数')
	parser.add_argument('--format', choices=('text', 'json'), default='text', help='输出格式')

def _add_doctor(sub: argparse._SubParsersAction) -> None:
	parser = sub.add_parser('doctor', help='诊断')
	parser.add_argument('--format', choices=('text', 'json'), default='text', help='输出格式')

def _add_gaps(sub: argparse._SubParsersAction) -> None:
	parser = sub.add_parser('gaps', help='缺口清单')
	parser.add_argument('--limit', type=int, default=50, help='最多列出条数（默认 50）')
	parser.add_argument('--clear', action='store_true', help='先清空缺口清单')
	parser.add_argument('--format', choices=('text', 'json'), default='text', help='输出格式')

def _add_eval(sub: argparse._SubParsersAction) -> None:
	parser = sub.add_parser('eval', help='评测')
	parser.add_argument('queries', help='JSONL 评测集路径')
	parser.add_argument('--modes', default='fused', help='逗号分隔的模式：lexical,dense,fused,rerank')
	parser.add_argument('--k', type=int, default=5, help='默认截断条数（默认 5）')
	parser.add_argument('--format', choices=('text', 'json'), default='text', help='输出格式')

def _add_models(sub: argparse._SubParsersAction) -> None:
	parser = sub.add_parser('models', help='模型管理')
	download = parser.add_subparsers(dest='models_command').add_parser('download', help='下载默认向量模型')
	download.add_argument('--force', action='store_true', help='即使校验通过也重新下载')
	download.add_argument('--format', choices=('text', 'json'), default='text', help='输出格式')

def _use_utf8_streams() -> None:
	"""Output is always UTF-8, even on Windows code pages (FR-1003). 输出固定为 UTF-8，Windows 代码页下也一样。"""
	for stream in (sys.stdout, sys.stderr):
		if hasattr(stream, 'reconfigure'):
			stream.reconfigure(encoding='utf-8')

def main(argv: list[str] | None = None) -> int:
	_use_utf8_streams()
	parser = build_parser()
	_LAST_PARSE_ERROR.clear()
	try:
		args = parser.parse_args(argv)
	except SystemExit as exc:
		code = exc.code if isinstance(exc.code, int) else 2
		if code == 2 and _wants_json(argv):
			message = _LAST_PARSE_ERROR[-1] if _LAST_PARSE_ERROR else '参数错误'
			_pretty_json(ErrorResult(code=2, message=message).to_dict())
		return code
	if args.command is None:
		parser.print_help()
		return 0
	format_name = getattr(args, 'format', 'text')
	try:
		return _dispatch(args, parser)
	except WenSuoError as exc:
		return _emit_error(exc, format_name)
	except BrokenPipeError:
		return 0
	except (sqlite3.Error, OSError) as exc:
		return _emit_error(WenSuoRuntimeError(f'运行错误：{exc}'), format_name)
	except KeyboardInterrupt:
		return 130

def _dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
	if args.command == 'models':
		if getattr(args, 'models_command', None) != 'download':
			parser.parse_args(['models', '--help'])
			return 0
		config = load_config(resolve_root(args.root, args.kb)).with_threads(getattr(args, 'threads', None))
		paths = download_model(config.models_dir, force=args.force)
		if args.format == 'json':
			_pretty_json({'schema_version': 1, 'files': [str(path) for path in paths]})
		else:
			for path in paths:
				print(path)
		return 0
	if args.command == 'sync':
		return _run_sync(args)
	kb = _open_kb(args)
	try:
		handler = {
			'search': _run_search,
			'read': _run_read,
			'show': _run_show,
			'docs': _run_docs,
			'scopes': _run_scopes,
			'doctor': _run_doctor,
			'gaps': _run_gaps,
			'eval': _run_eval,
		}[args.command]
		return handler(kb, args)
	finally:
		kb.close()

def _open_kb(args: argparse.Namespace) -> KnowledgeBase:
	root = resolve_root(args.root, args.kb)
	kb = KnowledgeBase(root)
	threads = getattr(args, 'threads', None)
	if threads is not None:
		kb.config = kb.config.with_threads(threads)
	return kb

def _run_search(kb: KnowledgeBase, args: argparse.Namespace) -> int:
	filters = Filters(
		scope=tuple(args.scope or ()),
		path_prefix=args.path_prefix,
		tags=tuple(args.tags or ()),
		file_types=tuple(args.file_types or ()),
		modified_after=parse_modified_after(args.modified_after) if args.modified_after else None,
	)
	options = SearchOptions(
		filters=filters,
		min_score=args.min_score,
		max_chars=args.max_chars,
		sync=not args.no_sync,
		entry='cli',
		expand_section=args.expand_section,
	)
	result = kb.search(args.query, top_k=args.top_k, options=options)
	if args.format == 'json':
		_pretty_json(result.to_dict())
	else:
		_render_search_text(result, args.max_chars)
	return 0 if result.results else 1

def _run_read(kb: KnowledgeBase, args: argparse.Namespace) -> int:
	options = ReadOptions(offset=args.offset, max_chars=args.max_chars, sync=not args.no_sync)
	result = kb.read(args.doc_id, section=args.section, options=options)
	if result is None:
		label = f'找不到文档或章节：{args.doc_id}' + (f' > {args.section}' if args.section else '')
		return _emit_not_found(label, args.format)
	if args.format == 'json':
		_pretty_json(result.to_dict())
	else:
		sys.stdout.write(result.text)
		if result.text and not result.text.endswith('\n'):
			sys.stdout.write('\n')
	return 0

def _run_show(kb: KnowledgeBase, args: argparse.Namespace) -> int:
	result = kb.show(args.chunk_id)
	if result is None:
		return _emit_not_found(f'找不到段：{args.chunk_id}', args.format)
	if args.format == 'json':
		_pretty_json(result.to_dict())
	else:
		position = _format_location(result.location)
		header = f'{result.chunk_id} {result.doc_id}' + (f' {position}' if position else '')
		print(header)
		if result.title_path:
			print(' > '.join(result.title_path))
		print(result.text)
	return 0

def _run_docs(kb: KnowledgeBase, args: argparse.Namespace) -> int:
	filters = Filters(scope=tuple(args.scope or ()), path_prefix=args.path_prefix)
	result = kb.list_docs(options=ListDocsOptions(filters=filters, sync=not args.no_sync))
	if args.format == 'json':
		_pretty_json(result.to_dict())
	else:
		print('doc_id\tscope\tchunks\tmodified\ttitle')
		for doc in result.docs:
			fields = [doc.doc_id, doc.scope, str(doc.chunks), doc.modified, doc.doc_title]
			print('\t'.join(fields))
	return 0

def _run_scopes(kb: KnowledgeBase, args: argparse.Namespace) -> int:
	result = kb.list_scopes()
	if args.format == 'json':
		_pretty_json(result.to_dict())
	else:
		print('scope\tdocs\tdescription')
		for scope in result.scopes:
			print(f'{scope.scope}\t{scope.docs}\t{scope.description or ""}')
	return 0

def _run_sync(args: argparse.Namespace) -> int:
	kb = _open_kb(args)
	try:

		def progress(done: int, total: int) -> None:
			if total <= 20:
				return
			end = '\n' if done == total else ''
			sys.stderr.write(f'\r同步 {done}/{total}{end}')
			sys.stderr.flush()

		report = kb.sync(threads=args.threads, wait=True, progress=progress)
	finally:
		kb.close()
	if args.format == 'json':
		_pretty_json(report.to_dict())
	else:
		_print_sync_report(report)
	return 0

def _run_doctor(kb: KnowledgeBase, args: argparse.Namespace) -> int:
	result = kb.doctor()
	if args.format == 'json':
		_pretty_json(result.to_dict())
	else:
		print(f'根目录: {result.root}')
		print(f'索引目录: {result.index_dir}')
		print(f'配置文件: {", ".join(result.config_files) or "无"}')
		print(f'文档数: {result.docs}')
		print(f'段数: {result.chunks}')
		print('专题: ' + _pairs(result.scopes))
		print('文件类型: ' + _pairs(result.file_types))
		dense_state = '就绪' if result.dense['ready'] else '未就绪'
		identity = f'，身份={result.dense["identity"][:16]}' if result.dense.get('identity') else ''
		print(f'向量模型: {result.dense["model"]}（{dense_state}，启用={result.dense["enabled"]}{identity}）')
		if result.rerank['enabled']:
			rerank_state = '就绪' if result.rerank['ready'] else '未就绪'
			rerank_identity = f'，身份={result.rerank["identity"]}' if result.rerank.get('identity') else ''
			print(f'重排模型: {result.rerank["model_dir"]}（{rerank_state}{rerank_identity}）')
		else:
			print('重排模型: 未启用')
		print(f'索引大小: {_format_bytes(result.index_bytes)}')
		for item in result.skipped:
			print(f'跳过: {item.doc_id}（{item.reason}）')
		for item in result.failed:
			print(f'失败: {item.doc_id}（{item.reason}）')
	return 0

def _run_gaps(kb: KnowledgeBase, args: argparse.Namespace) -> int:
	if args.clear:
		count = kb.clear_gaps()
		print(f'已清空 {count} 条缺口', file=sys.stderr)
	result = kb.gaps(limit=args.limit)
	if args.format == 'json':
		_pretty_json(result.to_dict())
	else:
		print('次数\t最后出现\t入口\t查询')
		for gap in result.gaps:
			print(f'{gap.count}\t{gap.last_seen}\t{gap.entry}\t{gap.query}')
	return 0

def _run_eval(kb: KnowledgeBase, args: argparse.Namespace) -> int:
	modes = [mode.strip() for mode in args.modes.split(',') if mode.strip()]
	result = kb.evaluate(args.queries, modes, k=args.k)
	if args.format == 'json':
		_pretty_json(result.to_dict())
	else:
		print(f'k={result.k}')
		for mode, metrics in result.modes.items():
			print(
				f'{mode}: hit@k={_metric(metrics.hit_at_k)} top1={_metric(metrics.top1)} '
				f'mrr@k={_metric(metrics.mrr_at_k)} '
				f'answerable_return_rate={_metric(metrics.answerable_return_rate)} '
				f'false_return_rate={_metric(metrics.false_return_rate)} n={metrics.n}'
			)
	return 0

def _render_search_text(result, max_chars: int) -> None:
	if result.missing_identifiers:
		print('知识库中未出现：' + ', '.join(result.missing_identifiers))
		if result.results:
			print()
	for index, hit in enumerate(result.results):
		if index:
			print()
		position = _format_location(hit.location)
		line = f'[{hit.rank}] {hit.score:.4f} {hit.doc_id}' + (f' {position}' if position else '')
		print(line)
		if hit.title_path:
			print(' > '.join(hit.title_path))
		text = hit.text
		if hit.truncated:
			text = text + '…'
		print(text)

def _format_location(location: Location) -> str:
	if location.kind == 'lines':
		return f'L{location.start}-{location.end}'
	if location.kind == 'page':
		return f'p{location.start}-{location.end}'
	if location.kind == 'slide':
		return f's{location.start}-{location.end}'
	if location.kind == 'sheet':
		return f'{location.sheet}!r{location.row_start}-{location.row_end}'
	return ''

def _print_sync_report(report) -> None:
	print(
		f'新增 {len(report.added)}，修改 {len(report.modified)}，删除 {len(report.deleted)}，'
		f'跳过 {len(report.skipped)}，失败 {len(report.failed)}，段 {report.chunks_total}，'
		f'耗时 {report.seconds:.2f}s',
		file=sys.stderr,
	)
	for doc_id in report.failed:
		print(f'失败: {doc_id}', file=sys.stderr)

def _emit_error(exc: WenSuoError, format_name: str) -> int:
	print(exc.message, file=sys.stderr)
	if format_name == 'json':
		_pretty_json(ErrorResult(code=exc.code, message=exc.message).to_dict())
	return exc.code

def _emit_not_found(message: str, format_name: str) -> int:
	print(message, file=sys.stderr)
	if format_name == 'json':
		_pretty_json(ErrorResult(code=1, message=message).to_dict())
	return 1

def _wants_json(argv: list[str] | None) -> bool:
	values = list(argv) if argv is not None else sys.argv[1:]
	for index, value in enumerate(values):
		if value == '--format' and index + 1 < len(values):
			return values[index + 1] == 'json'
		if value == '--format=json':
			return True
	return False

def _pretty_json(data: dict[str, Any]) -> None:
	print(json.dumps(data, ensure_ascii=False, indent=2))

def _metric(value: float | None) -> str:
	return 'null' if value is None else f'{value:.4f}'

def _pairs(mapping: dict[str, Any]) -> str:
	return ', '.join(f'{key}={value}' for key, value in mapping.items()) or '无'

def _format_bytes(size: int) -> str:
	if size < 1024:
		return f'{size} B'
	if size < 1024 * 1024:
		return f'{size / 1024:.1f} KB'
	return f'{size / (1024 * 1024):.1f} MB'


if __name__ == '__main__':
	raise SystemExit(main())
