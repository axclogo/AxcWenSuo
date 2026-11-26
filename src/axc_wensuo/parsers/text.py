"""Plain-text parser. 纯文本解析器。"""

from __future__ import annotations

from axc_wensuo.parsers.base import Block, ParsedDocument, document_title


def decode_text(data: bytes, *, path: str = '') -> tuple[str, str]:
	"""UTF-8 first, then GB18030. 先 UTF-8，再 GB18030。"""
	for encoding in ('utf-8-sig', 'gb18030'):
		try:
			return data.decode(encoding), encoding
		except UnicodeDecodeError:
			continue
	from axc_wensuo.parsers.base import ParseError

	raise ParseError(f'无法按 UTF-8 或 GB18030 解码{("：" + path) if path else ""}')

def parse_text(doc_id: str, text: str) -> ParsedDocument:
	lines = text.splitlines(keepends=True)
	offsets: list[int] = []
	position = 0
	for line in lines:
		offsets.append(position)
		position += len(line)
	offsets.append(position)
	blocks: list[Block] = []
	index = 0
	while index < len(lines):
		if not lines[index].strip():
			index += 1
			continue
		end = index
		while end + 1 < len(lines) and lines[end + 1].strip():
			end += 1
		start_offset = offsets[index]
		end_offset = offsets[end] + len(lines[end].rstrip('\r\n'))
		blocks.append(
			Block(
				kind='paragraph',
				start=start_offset,
				end=end_offset,
				text=text[start_offset:end_offset],
				line_start=index + 1,
				line_end=end + 1,
			)
		)
		index = end + 1
	return ParsedDocument(
		doc_id=doc_id,
		doc_title=document_title(None, blocks, doc_id),
		text=text,
		blocks=blocks,
		format='text',
	)
