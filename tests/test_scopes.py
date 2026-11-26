from __future__ import annotations

from helpers import yaml_config

FM = '---\nscope: 文件头专题\ntitle: 文件头专题文档\n---\n\n# 标题\n\n正文内容。\n'


def test_scope_four_levels(kb_factory):
	config = yaml_config(
		scope_rules=[{'glob': '**/*报销*', 'scope': '财务'}, {'glob': '运维/**', 'scope': '运维规则'}],
		scopes={'财务': '报销与发票', '运维规则': '部署与巡检'},
	)
	kb = kb_factory(
		{
			'文件头.md': FM,
			'财务/报销单.md': '# 报销单\n\n正文。\n',
			'运维/部署.md': '# 部署\n\n正文。\n',
			'根文件.md': '# 根文件\n\n正文。\n',
		},
		config=config,
	)
	scopes = {doc.doc_id: doc.scope for doc in kb.list_docs().docs}
	assert scopes['文件头.md'] == '文件头专题'
	assert scopes['财务/报销单.md'] == '财务'
	assert scopes['运维/部署.md'] == '运维规则'
	assert scopes['根文件.md'] == '_root'
	assert kb.config.scopes['财务'] == '报销与发票'


def test_scope_rule_order(kb_factory):
	config = yaml_config(scope_rules=[{'glob': '**/*.md', 'scope': '第一'}, {'glob': '**/*.md', 'scope': '第二'}])
	kb = kb_factory({'a.md': '# A\n\n正文。\n'}, config=config)
	assert kb.list_docs().docs[0].scope == '第一'


def test_list_scopes_with_descriptions(kb_factory):
	config = yaml_config(scopes={'运维': '部署与巡检', '空专题': '没有文档'})
	kb = kb_factory({'运维/部署.md': '# 部署\n\n正文。\n'}, config=config)
	result = kb.list_scopes()
	by_name = {scope.scope: scope for scope in result.scopes}
	assert by_name['运维'].docs == 1
	assert by_name['运维'].description == '部署与巡检'
	assert by_name['空专题'].docs == 0
	assert by_name['空专题'].description == '没有文档'


def test_scope_filter(kb_factory):
	config = yaml_config(scopes={'财务': '财务专题'})
	kb = kb_factory(
		{
			'财务/报销.md': '# 报销\n\n差旅报销流程。\n',
			'运维/部署.md': '# 部署\n\n部署流程。\n',
		},
		config=config,
	)
	docs = kb.list_docs(scope=['财务']).docs
	assert [doc.doc_id for doc in docs] == ['财务/报销.md']
	results = kb.search('流程', scope=['财务']).results
	assert {hit.doc_id for hit in results} == {'财务/报销.md'}
