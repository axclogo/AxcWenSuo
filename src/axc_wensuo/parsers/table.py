"""CSV/TSV parser. CSV/TSV 解析器。"""

from __future__ import annotations

import csv
import io

from axc_wensuo.parsers.base import Block, ParsedDocument, Table, document_title, render_table


def parse_table(doc_id: str, text: str, *, delimiter: str = ',') -> ParsedDocument:
	rows: list[list[str]] = []
	row_lines: list[int] = []
	reader = csv.reader(io.StringIO(text), delimiter=delimiter)
	for row in reader:
		if not row or all(not cell.strip() for cell in row):
			continue
		rows.append(row)
		row_lines.append(reader.line_num)
	blocks: list[Block] = []
	if rows:
		table = Table(rows=rows, header_rows=1, row_lines=list(row_lines))
		rendered = render_table(rows)
		blocks.append(
			Block(
				kind='table',
				start=0,
				end=len(rendered),
				text=rendered,
				line_start=1,
				line_end=len(text.splitlines()) or 1,
				table=table,
			)
		)
	return ParsedDocument(
		doc_id=doc_id,
		doc_title=document_title(None, blocks, doc_id),
		text=rendered if rows else '',
		blocks=blocks,
		format='table',
	)
