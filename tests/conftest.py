from __future__ import annotations

import os
from pathlib import Path

import pytest
from helpers import write, yaml_config

from axc_wensuo import KnowledgeBase

FIXTURES = Path(__file__).parent / 'fixtures' / 'corpus'


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
	home = tmp_path_factory.mktemp('home')
	monkeypatch.setenv('HOME', str(home))
	monkeypatch.setenv('USERPROFILE', str(home))
	monkeypatch.setenv('XDG_CONFIG_HOME', str(home / '.config'))
	monkeypatch.delenv('APPDATA', raising=False)
	yield home


@pytest.fixture
def kb_factory(tmp_path):
	created: list[KnowledgeBase] = []
	counter = {'value': 0}

	def factory(
		files: dict[str, str | bytes] | None = None,
		config: str | None = None,
	) -> KnowledgeBase:
		root = tmp_path / f'kb{counter["value"]}'
		counter['value'] += 1
		root.mkdir()
		(root / 'wensuo.yaml').write_text(config or yaml_config(), encoding='utf-8')
		for relative, content in (files or {}).items():
			path = root / relative
			path.parent.mkdir(parents=True, exist_ok=True)
			if isinstance(content, bytes):
				path.write_bytes(content)
			else:
				write(root, relative, content)
		kb = KnowledgeBase(root)
		created.append(kb)
		return kb

	yield factory
	for kb in created:
		kb.close()


@pytest.fixture
def corpus_copy(tmp_path) -> Path:
	import shutil

	target = tmp_path / 'corpus'
	shutil.copytree(FIXTURES, target, ignore=shutil.ignore_patterns('.wensuo', '__pycache__'))
	return target


def set_mtime(path: Path, seconds: float) -> None:
	os.utime(path, (seconds, seconds))
