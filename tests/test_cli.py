from axc_wensuo import __version__
from axc_wensuo.cli import main


def test_version(capsys):
	assert main(['--version']) == 0
	assert __version__ in capsys.readouterr().out

def test_no_args_prints_help(capsys):
	assert main([]) == 0
	assert 'usage: ws' in capsys.readouterr().out

def test_help_exits_zero(capsys):
	assert main(['--help']) == 0
	assert 'usage: ws' in capsys.readouterr().out


def test_cli_output_is_utf8_under_legacy_code_page(tmp_path):
	import json
	import os
	import subprocess
	import sys

	from helpers import write, yaml_config

	root = tmp_path / 'kb'
	write(root, 'wensuo.yaml', yaml_config())
	write(root, '甲.md', '# 甲\n\n鲸鱼。\n')
	# Simulate a Windows console code page that cannot encode Chinese. 模拟无法编码中文的 Windows 代码页。
	env = {**os.environ, 'PYTHONIOENCODING': 'cp1252', 'PYTHONUTF8': '0'}
	command = [sys.executable, '-m', 'axc_wensuo.cli', '--root', str(root), 'search', '鲸鱼', '--format', 'json']
	result = subprocess.run(command, capture_output=True, env=env, timeout=120)
	assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
	assert json.loads(result.stdout.decode('utf-8'))['results'][0]['doc_id'] == '甲.md'
	sync = subprocess.run([*command[:5], 'sync'], capture_output=True, env=env, timeout=120)
	assert '新增' in sync.stderr.decode('utf-8')
