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
