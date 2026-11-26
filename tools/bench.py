#!/usr/bin/env python3
"""非功能需求基准测试（需求第 10 节）。

用固定随机种子生成语料与查询，测量查询延迟、首次建索引、无变化同步、改一个文件后的同步、命令行
冷启动等指标。默认关闭向量（无需下载模型）；``--dense`` 需要先准备好默认向量模型。

示例::

	python tools/bench.py --chunks 2500 --queries 200 --threads 1
	python tools/bench.py --sync-files 10000 --json
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from axc_wensuo import KnowledgeBase
from axc_wensuo.errors import WenSuoError

WORDS = [
	'用户',
	'订单',
	'支付',
	'库存',
	'报表',
	'接口',
	'部署',
	'巡检',
	'环境变量',
	'数据库',
	'连接池',
	'缓存',
	'日志',
	'告警',
	'配置',
	'权限',
	'角色',
	'审批',
	'发票',
	'对账',
	'调度',
	'任务',
	'队列',
	'重试',
	'超时',
	'分页',
	'索引',
	'迁移',
	'备份',
	'回滚',
]
IDENTIFIERS = ['user_id', 'order_no', 'max_conn', 'v2.1', 'api_key', 'cache_ttl', 'db_url', 'retry_count']


def write_corpus(root: Path, chunks: int, rng: random.Random) -> list[Path]:
	files: list[Path] = []
	per_file = 25
	file_count = max(1, chunks // per_file)
	chunk_index = 0
	for file_index in range(file_count):
		lines = [f'# 文档 {file_index}', '']
		for section in range(per_file):
			lines.append(f'## 小节 {file_index}-{section}')
			lines.append('')
			identifier = rng.choice(IDENTIFIERS)
			body = '。'.join(' '.join(rng.choice(WORDS) for _ in range(12)) for _ in range(3))
			lines.append(f'{body}。标识符 {identifier} 用于测试。')
			lines.append('')
			chunk_index += 1
		path = root / f'批次{file_index // 10:02d}' / f'文档{file_index:03d}.md'
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text('\n'.join(lines), encoding='utf-8')
		files.append(path)
	return files


def make_queries(count: int, rng: random.Random) -> list[str]:
	queries: list[str] = []
	for index in range(count):
		if index % 2 == 0:
			queries.append(' '.join(rng.choice(WORDS) for _ in range(2)))
		else:
			queries.append(f'{rng.choice(IDENTIFIERS)} {rng.choice(WORDS)}')
	return queries


def percentile(values: list[float], fraction: float) -> float:
	ordered = sorted(values)
	position = (len(ordered) - 1) * fraction
	lower = int(position)
	upper = min(lower + 1, len(ordered) - 1)
	weight = position - lower
	return ordered[lower] * (1 - weight) + ordered[upper] * weight


def measure_queries(kb: KnowledgeBase, queries: list[str], warmup: int = 10) -> dict[str, float]:
	for query in queries[:warmup]:
		kb.search(query, sync=False)
	samples: list[float] = []
	for query in queries:
		started = time.perf_counter()
		kb.search(query, sync=False)
		samples.append((time.perf_counter() - started) * 1000)
	return {
		'p50_ms': round(percentile(samples, 0.50), 2),
		'p95_ms': round(percentile(samples, 0.95), 2),
		'mean_ms': round(statistics.fmean(samples), 2),
		'samples': len(samples),
	}


def measure_sync_10k(workdir: Path, rng: random.Random) -> dict[str, float]:
	root = workdir / 'sync-bench'
	root.mkdir(parents=True, exist_ok=True)
	(root / 'wensuo.yaml').write_text('dense:\n  enabled: false\n', encoding='utf-8')
	for index in range(10_000):
		directory = root / f'{index % 100:02d}'
		directory.mkdir(exist_ok=True)
		(directory / f'{index:05d}.md').write_text(f'# 文件 {index}\n\n内容 {index}。\n', encoding='utf-8')
	kb = KnowledgeBase(root)
	try:
		kb.sync()
		times: list[float] = []
		for _ in range(10):
			started = time.perf_counter()
			kb.sync()
			times.append((time.perf_counter() - started) * 1000)
	finally:
		kb.close()
	shutil.rmtree(root, ignore_errors=True)
	return {'max_ms': round(max(times), 2), 'p95_ms': round(percentile(times, 0.95), 2)}


def cold_start(root: Path, query: str) -> float:
	command = [sys.executable, '-m', 'axc_wensuo.cli', '--root', str(root), 'search', query]
	started = time.perf_counter()
	subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
	return round((time.perf_counter() - started) * 1000, 2)


def main() -> int:
	parser = argparse.ArgumentParser(description='AxcWenSuo 基准测试')
	parser.add_argument('--chunks', type=int, default=2500)
	parser.add_argument('--queries', type=int, default=200)
	parser.add_argument('--threads', type=int, default=1)
	parser.add_argument('--workdir', default=None)
	parser.add_argument('--dense', action='store_true', help='启用默认向量模型（需已下载）')
	parser.add_argument('--rerank-model-dir', default=None)
	parser.add_argument('--sync-files', type=int, default=0, help='额外测量 N 个文件无变化同步（如 10000）')
	parser.add_argument('--json', action='store_true')
	args = parser.parse_args()

	rng = random.Random(20261126)
	workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix='wensuo-bench-'))
	workdir.mkdir(parents=True, exist_ok=True)
	root = workdir / 'kb'
	root.mkdir(exist_ok=True)
	config = {'dense': {'enabled': args.dense}, 'threads': args.threads}
	if args.rerank_model_dir:
		config['rerank'] = {'model_dir': args.rerank_model_dir}
	(root / 'wensuo.yaml').write_text(json.dumps(config), encoding='utf-8')
	write_corpus(root, args.chunks, rng)
	queries = make_queries(args.queries, rng)

	report: dict[str, object] = {'chunks_target': args.chunks, 'queries': args.queries}
	kb = KnowledgeBase(root)
	try:
		started = time.perf_counter()
		report['first_index_seconds'] = round(kb.sync(threads=args.threads).seconds, 3)
		report['first_index_wall_seconds'] = round(time.perf_counter() - started, 3)
		report['chunks_total'] = kb.store.count_chunks()
		report['query_latency'] = measure_queries(kb, queries)

		first_query = queries[0]
		report['cold_start_ms'] = cold_start(root, first_query)

		target = next(iter(sorted((root / '批次00').glob('*.md'))))
		target.write_text(
			target.read_text(encoding='utf-8') + '\n\n## 新增小节\n\n改一个文件后的同步测量。\n',
			encoding='utf-8',
		)
		started = time.perf_counter()
		kb.sync(threads=args.threads)
		report['single_file_sync_ms'] = round((time.perf_counter() - started) * 1000, 2)
	finally:
		kb.close()

	if args.sync_files:
		report['no_change_sync_10k'] = measure_sync_10k(workdir, rng)

	try:
		shutil.rmtree(workdir)
	except OSError:
		pass

	if args.json:
		print(json.dumps(report, ensure_ascii=False, indent=2))
	else:
		print(f'语料段数: {report["chunks_total"]}')
		print(f'首次建索引: {report["first_index_wall_seconds"]}s')
		latency = report['query_latency']
		print(f'查询延迟 p50/p95/mean: {latency["p50_ms"]} / {latency["p95_ms"]} / {latency["mean_ms"]} ms')
		print(f'命令行冷启动: {report["cold_start_ms"]} ms')
		print(f'改一个文件后同步: {report["single_file_sync_ms"]} ms')
		if 'no_change_sync_10k' in report:
			print(f'1 万文件无变化同步: {report["no_change_sync_10k"]}')
	return 0


if __name__ == '__main__':
	try:
		raise SystemExit(main())
	except WenSuoError as error:
		print(error.message, file=sys.stderr)
		raise SystemExit(error.code) from error
