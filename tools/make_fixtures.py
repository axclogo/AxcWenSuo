#!/usr/bin/env python3
"""生成公开测试语料 ``tests/fixtures/corpus/``（需求第 11.1 节）。

生成的文件都是虚构内容，不含任何真实组织信息。二进制格式需要额外依赖，用临时环境运行::

	uv run --with python-docx --with python-pptx --with openpyxl --with reportlab \\
		python tools/make_fixtures.py

Markdown、TXT、CSV 是本脚本直接写出的文本文件。
"""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / 'tests' / 'fixtures' / 'corpus'

GUIDE = """---
title: 部署手册
tags: [部署, 运维]
aliases: [deploy, 上线指南]
scope: 运维
---

# 部署手册

本文介绍虚构系统「蓝鲸」的部署流程。

## 环境变量

服务读取以下环境变量：

- `DATABASE_URL`：数据库连接串
- `MAX_CONN`：连接池上限
- `LOG_LEVEL`：日志级别

### 示例

```bash
export DATABASE_URL=postgres://example/db
export MAX_CONN=32
```

## 发布步骤

1. 拉取镜像
2. 执行数据库迁移
3. 滚动重启

## 回滚

回滚前先备份 `user_id` 映射表。

| 步骤 | 命令 | 说明 |
| --- | --- | --- |
| 停止 | `systemctl stop lanjing` | 停止服务 |
| 回滚 | `lanjing rollback --to v2.1` | 回到旧版本 |
| 启动 | `systemctl start lanjing` | 启动服务 |
"""

CODE = """# 代码示例

下面展示如何读取配置，代码块中的井号不是标题。

```python
# 这是代码块里的注释行，不应被当作 Markdown 标题
def read_config(path):
	# 另一个注释
	return open(path).read()
```

## 说明

配置读取函数不会校验内容。
"""

LONG_TABLE_HEADER = '| 编号 | 名称 | 负责人 | 说明 |\n| --- | --- | --- | --- |\n'
LONG_TABLE = (
	'# 设备台账\n\n'
	+ LONG_TABLE_HEADER
	+ ''.join(
		f'| {index:03d} | 设备{index} | 值班员{index % 7} | 第 {index} 台虚构设备的巡检记录 |\n'
		for index in range(1, 121)
	)
)

NOTES = """蓝鲸系统运维笔记

第一节：每日巡检
每天上午检查磁盘占用与队列长度，异常时记录到值班表。

第二节：故障处理
遇到连接池耗尽时，先确认 MAX_CONN，再排查慢查询。
"""
NOTES += '\n'

CSV_ROWS = [
	['工号', '姓名', '部门', '权限'],
	*[
		[f'E{index:03d}', f'员工{index}', '研发部' if index % 2 else '运维部', '读写' if index % 3 else '只读']
		for index in range(1, 41)
	],
]

HTML = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>接口说明</title></head>
<body>
<h1>接口说明</h1>
<h2>鉴权</h2>
<p>所有接口使用 Bearer Token 鉴权。</p>
<h2>错误码</h2>
<table>
<tr><th>错误码</th><th>含义</th></tr>
<tr><td>401</td><td>未认证</td></tr>
<tr><td>403</td><td>无权限</td></tr>
</table>
</body>
</html>
"""


def write_text_files() -> None:
	CORPUS.mkdir(parents=True, exist_ok=True)
	(CORPUS / '运维').mkdir(exist_ok=True)
	(CORPUS / '数据').mkdir(exist_ok=True)
	(CORPUS / '运维' / '部署手册.md').write_text(GUIDE, encoding='utf-8')
	(CORPUS / '运维' / '代码示例.md').write_text(CODE, encoding='utf-8')
	(CORPUS / '数据' / '设备台账.md').write_text(LONG_TABLE, encoding='utf-8')
	(CORPUS / '运维' / '笔记.txt').write_bytes(NOTES.encode('gb18030'))
	with (CORPUS / '数据' / '员工.csv').open('w', encoding='utf-8', newline='') as handle:
		writer = csv.writer(handle)
		writer.writerows(CSV_ROWS)
	(CORPUS / '接口说明.html').write_text(HTML, encoding='utf-8')


def write_pdf() -> None:
	from reportlab.lib import colors
	from reportlab.lib.pagesizes import A4
	from reportlab.lib.styles import ParagraphStyle
	from reportlab.pdfbase import pdfmetrics
	from reportlab.pdfbase.cidfonts import UnicodeCIDFont
	from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

	pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
	path = CORPUS / '数据' / '巡检报告.pdf'
	document = SimpleDocTemplate(str(path), pagesize=A4, title='虚构巡检报告')
	style = ParagraphStyle('Body', fontName='STSong-Light', fontSize=11, leading=16)
	heading_style = ParagraphStyle('Heading', fontName='STSong-Light', fontSize=16, leading=22)

	table_data = [['巡检项', '数值', '状态']]
	table_data += [[f'检查项 {index}', str(index * 10), '正常' if index % 2 else '关注'] for index in range(1, 9)]
	table = Table(table_data, colWidths=[160, 100, 100])
	table.setStyle(
		TableStyle(
			[
				('FONTNAME', (0, 0), (-1, -1), 'STSong-Light'),
				('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
				('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
			]
		)
	)

	story = [
		Paragraph('虚构巡检报告', ParagraphStyle('Title', parent=style, fontSize=18)),
		Spacer(1, 12),
		Paragraph('第一页：系统概览。蓝鲸系统运行正常，磁盘与队列均在阈值内。', style),
		Spacer(1, 12),
		Paragraph('本页用于验证 PDF 的页码定位与标题层级。', style),
		PageBreak(),
		Paragraph('第二页：巡检表格', heading_style),
		Spacer(1, 12),
		table,
		Spacer(1, 12),
		Paragraph('表格内容为虚构数据。', style),
		PageBreak(),
		Paragraph('第三页：结论', heading_style),
		Spacer(1, 12),
		Paragraph('所有检查已完成。本 PDF 的唯一标识是 ZEPHYR CODE。', style),
	]
	document.build(story)


def write_docx() -> None:
	from docx import Document

	document = Document()
	document.add_heading('年度总结', level=1)
	document.add_paragraph('本文件为虚构的年度总结，用于解析测试。')
	document.add_heading('目标完成情况', level=2)
	document.add_paragraph('完成率 87%，未完成项集中在数据治理。')
	table = document.add_table(rows=3, cols=2)
	table.cell(0, 0).text = '指标'
	table.cell(0, 1).text = '数值'
	table.cell(1, 0).text = '收入'
	table.cell(1, 1).text = '1200'
	table.cell(2, 0).text = '成本'
	table.cell(2, 1).text = '800'
	document.save(CORPUS / '数据' / '年度总结.docx')


def write_pptx() -> None:
	from pptx import Presentation

	prs = Presentation()
	first = prs.slides.add_slide(prs.slide_layouts[1])
	first.shapes.title.text = '项目汇报'
	first.placeholders[1].text = '第一页：背景与目标'
	second = prs.slides.add_slide(prs.slide_layouts[1])
	second.shapes.title.text = '里程碑'
	second.placeholders[1].text = '第二页：关键里程碑 ZEPHYR-PPT'
	prs.save(CORPUS / '运维' / '项目汇报.pptx')


def write_xlsx() -> None:
	from openpyxl import Workbook

	workbook = Workbook()
	first = workbook.active
	first.title = '收入'
	first.append(['月份', '金额', '备注'])
	for month in range(1, 13):
		first.append([f'{month} 月', month * 100, '虚构数据'])
	second = workbook.create_sheet('成本')
	second.append(['科目', '金额'])
	second.append(['人力', 5000])
	second.append(['设备', 2000])
	workbook.save(CORPUS / '数据' / '财务数据.xlsx')


def main() -> None:
	write_text_files()
	write_pdf()
	write_docx()
	write_pptx()
	write_xlsx()
	print(f'fixtures written to {CORPUS}')


if __name__ == '__main__':
	main()
