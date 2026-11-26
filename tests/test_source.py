from __future__ import annotations

import unicodedata

from helpers import list_docs, read, search


def test_doc_id_is_nfc_normalized(kb_factory):
	decomposed = unicodedata.normalize('NFD', 'café.md')
	kb = kb_factory({decomposed: '# café\n\n内容说明。\n'})
	documents = list_docs(kb).docs
	assert [doc.doc_id for doc in documents] == [unicodedata.normalize('NFC', 'café.md')]
	hit = search(kb, '内容说明').results[0]
	assert hit.doc_id == unicodedata.normalize('NFC', 'café.md')
	assert read(kb, hit.doc_id) is not None

def test_include_exclude_glob(kb_factory):
	config = 'dense: {enabled: false}\ninclude: ["**/*.md"]\nexclude: ["**/草稿/**"]\n'
	kb = kb_factory(
		{
			'正式/文档.md': '# 文档\n\n正式内容。\n',
			'草稿/文档.md': '# 草稿\n\n草稿内容。\n',
			'笔记.txt': '不是 Markdown。\n',
		},
		config=config,
	)
	report = kb.sync()
	assert [doc.doc_id for doc in list_docs(kb).docs] == ['正式/文档.md']
	assert '草稿/文档.md' in report.skipped
	assert '笔记.txt' in report.skipped
	reasons = {entry.doc_id: entry.reason for entry in kb.doctor().skipped}
	assert 'exclude' in reasons['草稿/文档.md']
	assert 'include' in reasons['笔记.txt']

def test_modify_filepath_nfc_scope(kb_factory):
	# 第一级子目录名使用 NFD，专题名统一为 NFC
	decomposed = unicodedata.normalize('NFD', 'café')
	kb = kb_factory({f'{decomposed}/文档.md': '# 文档\n\n内容。\n'})
	doc = list_docs(kb).docs[0]
	assert doc.scope == unicodedata.normalize('NFC', 'café')
	assert list_docs(kb, scope=[unicodedata.normalize('NFC', 'café')]).docs
