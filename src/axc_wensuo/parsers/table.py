"""CSV/TSV parser. CSV/TSV 解析器。"""

from __future__ import annotations

import csv
import io

from axc_wensuo.parsers.base import Block, ParsedDocument, ParseError, Table, document_title, render_table


def parse_table(doc_id: str, text: str, *, delimiter: str = ',') -> ParsedDocument:
	rows: list[list[str]] = []
	row_lines: list[int] = []
	row_ends: list[int] = []
	reader = csv.reader(io.StringIO(text), delimiter=delimiter)
	previous_end = 0
	try:
		for row in reader:
			start, previous_end = previous_end + 1, reader.line_num
			if not row or all(not cell.strip() for cell in row):
				continue
			rows.append(row)
			row_lines.append(start)
			row_ends.append(previous_end)
	except csv.Error as exc:
		raise ParseError(f'CSV/TSV 解析失败（第 {reader.line_num} 行附近）：{exc}') from exc
	blocks: list[Block] = []
	if rows:
		table = Table(rows=rows, header_rows=1, row_lines=row_lines, row_end_lines=row_ends)
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
