from __future__ import annotations

import asyncio
import json
import re
import socket
import sys

import pytest
from helpers import run_cli, search, write

DOC = '# 甲文档\n\n蓝色鲸鱼在深海游动。\n'


def test_cli_exit_codes(kb_factory):
	kb = kb_factory({'甲.md': DOC})
	root = kb.root
	assert run_cli(root, 'search', '鲸鱼').returncode == 0
	assert run_cli(root, 'search', 'zzzz').returncode == 1
	assert run_cli(root, 'read', '不存在.md').returncode == 1
	assert run_cli(root, 'show', 'deadbeefdeadbeef').returncode == 1
	assert run_cli(root, 'search', '鲸鱼', '--min-score', '0.5').returncode == 2
	assert run_cli(root, 'search', '!!!').returncode == 2
	assert run_cli(root, 'search', '鲸鱼', '--top-k', '0').returncode == 2

	error = run_cli(root, 'search', '!!!', '--format', 'json')
	assert error.returncode == 2
	payload = json.loads(error.stdout)
	assert payload['error']['code'] == 2
	assert payload['schema_version'] == 1

def test_missing_model_is_runtime_error(tmp_path):
	root = tmp_path / 'kb'
	root.mkdir()
	write(root, '甲.md', DOC)
	write(root, 'wensuo.yaml', 'dense:\n  enabled: true\n')
	result = run_cli(root, 'search', '鲸鱼')
	assert result.returncode == 3
	assert '向量模型未就绪' in result.stderr
	result = run_cli(root, 'search', '鲸鱼', '--format', 'json')
	assert result.returncode == 3
	assert json.loads(result.stdout)['error']['code'] == 3

def test_cli_text_format(kb_factory):
	kb = kb_factory({'甲.md': DOC})
	result = run_cli(kb.root, 'search', '鲸鱼')
	assert result.returncode == 0
	lines = result.stdout.splitlines()
	assert re.fullmatch(r'\[1\] \d+\.\d{4} 甲\.md L\d+-\d+', lines[0])
	assert lines[1] == '甲文档'
	assert any('鲸鱼' in line for line in lines[2:])

def test_cli_text_missing_identifiers_and_truncation(kb_factory):
	kb = kb_factory({'甲.md': '# 甲\n\n' + '长内容' * 50 + '\n'})
	result = run_cli(kb.root, 'search', 'zz_flag 长内容', '--max-chars', '20')
	lines = result.stdout.splitlines()
	assert lines[0] == '知识库中未出现：zz_flag'
	assert lines[1] == ''
	assert lines[2].startswith('[1] ')
	assert lines[-1].endswith('…')

def test_cli_json_structure(kb_factory):
	kb = kb_factory({'甲.md': DOC})
	result = run_cli(kb.root, 'search', '鲸鱼', '--format', 'json')
	payload = json.loads(result.stdout)
	assert payload['schema_version'] == 1
	assert payload['query'] == '鲸鱼'
	assert isinstance(payload['stale'], bool)
	assert payload['score_kind'] == 'rank_only'
	assert payload['identifiers'] == []
	assert payload['missing_identifiers'] == []
	hit = payload['results'][0]
	assert set(hit) == {
		'rank',
		'chunk_id',
		'doc_id',
		'doc_title',
		'scope',
		'title_path',
		'location',
		'score',
		'sources',
		'text',
		'truncated',
	}
	assert hit['location']['kind'] == 'lines'
	assert hit['sources']['lexical'] >= 1

def test_cli_docs_scopes_doctor_gaps(kb_factory):
	kb = kb_factory({'甲.md': DOC})
	docs = json.loads(run_cli(kb.root, 'docs', '--format', 'json').stdout)
	assert docs['docs'][0]['doc_id'] == '甲.md'
	scopes = json.loads(run_cli(kb.root, 'scopes', '--format', 'json').stdout)
	assert scopes['scopes'][0]['scope'] == '_root'

	sync = json.loads(run_cli(kb.root, 'sync', '--format', 'json').stdout)
	assert sync['schema_version'] == 1
	assert sync['chunks_total'] >= 1
	assert set(sync) >= {'added', 'modified', 'deleted', 'skipped', 'failed', 'chunks_total', 'seconds'}

	doctor = json.loads(run_cli(kb.root, 'doctor', '--format', 'json').stdout)
	assert doctor['docs'] == 1
	assert doctor['dense']['enabled'] is False
	assert set(doctor) >= {
		'root',
		'index_dir',
		'config_files',
		'docs',
		'chunks',
		'scopes',
		'file_types',
		'skipped',
		'failed',
		'dense',
		'rerank',
		'index_bytes',
	}

	run_cli(kb.root, 'search', 'missing_ident')
	gaps = json.loads(run_cli(kb.root, 'gaps', '--format', 'json').stdout)
	assert gaps['gaps'][0]['query'] == 'missing_ident'
	run_cli(kb.root, 'gaps', '--clear')
	assert json.loads(run_cli(kb.root, 'gaps', '--format', 'json').stdout)['gaps'] == []

def test_cli_read_text_and_offsets(kb_factory):
	text = '# 标题\n\n第一段内容。\n'
	kb = kb_factory({'甲.md': text})
	result = run_cli(kb.root, 'read', '甲.md')
	assert result.returncode == 0
	assert result.stdout == text
	result = run_cli(kb.root, 'read', '甲.md', '--offset', '2', '--max-chars', '5', '--format', 'json')
	payload = json.loads(result.stdout)
	assert payload['offset'] == 2
	assert payload['text'] == text[2:7]
	assert payload['next_offset'] == 7

def test_cli_version_and_help():
	assert 'ws ' in run_cli('.', '--version').stdout
	help_result = run_cli('.')
	assert help_result.returncode == 0
	assert 'usage: ws' in help_result.stdout

def test_python_api_matches_cli_json(kb_factory):
	kb = kb_factory({'甲.md': DOC})
	kb.sync()
	api = search(kb, '鲸鱼', sync=False).to_dict()
	cli = json.loads(run_cli(kb.root, 'search', '鲸鱼', '--format', 'json', '--no-sync').stdout)
	api.pop('stale')
	cli.pop('stale')
	assert api == cli

def test_mcp_matches_cli_json(kb_factory):
	pytest.importorskip('mcp')
	from mcp import ClientSession, StdioServerParameters
	from mcp.client.stdio import stdio_client

	kb = kb_factory({'甲.md': DOC})
	kb.sync()
	cli = json.loads(run_cli(kb.root, 'search', '鲸鱼', '--format', 'json', '--no-sync').stdout)
	cli.pop('stale')

	async def call():
		params = StdioServerParameters(
			command=sys.executable,
			args=['-m', 'axc_wensuo.cli', '--root', str(kb.root), 'mcp'],
		)
		async with stdio_client(params) as (read, write_stream):
			async with ClientSession(read, write_stream) as session:
				await session.initialize()
				tools = await session.list_tools()
				result = await session.call_tool('search', {'query': '鲸鱼'})
				return tools, result

	tools, result = asyncio.run(call())
	names = [tool.name for tool in tools.tools]
	assert names == ['search', 'read', 'list_docs', 'list_scopes']
	search_tool = next(tool for tool in tools.tools if tool.name == 'search')
	assert 'score_kind' in search_tool.description
	assert 'missing_identifiers' in search_tool.description
	assert '_root' in search_tool.description
	payload = json.loads(result.content[0].text)
	payload.pop('stale')
	assert payload == cli

def test_offline_search_does_not_open_socket(kb_factory, monkeypatch):
	kb = kb_factory({'甲.md': DOC})
	kb.sync()

	def blocked(*args, **kwargs):
		raise AssertionError('检索不应访问网络')

	monkeypatch.setattr(socket, 'socket', blocked)
	monkeypatch.setattr(socket, 'create_connection', blocked)
	result = search(kb, '鲸鱼', sync=False)
	assert result.results

def test_models_download_reports_unpublished(kb_factory):
	kb = kb_factory({'甲.md': DOC})
	result = run_cli(kb.root, 'models', 'download')
	assert result.returncode == 3
	assert '尚未发布' in result.stderr

def test_cli_sync_text_report_goes_to_stderr(kb_factory):
	kb = kb_factory({'甲.md': DOC})
	result = run_cli(kb.root, 'sync')
	assert result.returncode == 0
	assert result.stdout == ''
	assert '新增 1' in result.stderr

def test_mcp_server_exits_when_client_disconnects(kb_factory):
	pytest.importorskip('mcp')
	import subprocess

	kb = kb_factory({'甲.md': DOC})
	process = subprocess.Popen(
		[sys.executable, '-m', 'axc_wensuo.cli', '--root', str(kb.root), 'mcp'],
		stdin=subprocess.PIPE,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
	)
	assert process.stdin is not None
	process.stdin.close()
	assert process.wait(timeout=30) == 0

def test_cli_argparse_error_json(kb_factory):
	kb = kb_factory({'甲.md': DOC})
	result = run_cli(kb.root, 'search', '--top-k', 'abc', '--format', 'json')
	assert result.returncode == 2
	payload = json.loads(result.stdout)
	assert payload['error']['code'] == 2
	assert 'top-k' in payload['error']['message']

def test_sync_progress_for_many_files(kb_factory):
	files = {f'文档/{index:02d}.md': f'# 文档 {index}\n\n内容 {index}。\n' for index in range(25)}
	kb = kb_factory(files)
	result = run_cli(kb.root, 'sync')
	assert result.returncode == 0
	assert '同步 25/25' in result.stderr

def test_corrupt_index_is_runtime_error(kb_factory):
	kb = kb_factory({'甲.md': DOC})
	kb.sync()
	index_dir = kb.config.index_dir
	kb.close()
	for name in ('index.sqlite', 'index.sqlite-wal', 'index.sqlite-shm'):
		(index_dir / name).unlink(missing_ok=True)
	(index_dir / 'index.sqlite').write_bytes(b'not a sqlite database')
	result = run_cli(kb.root, 'search', '鲸鱼')
	assert result.returncode == 3
	assert '无法打开索引' in result.stderr
