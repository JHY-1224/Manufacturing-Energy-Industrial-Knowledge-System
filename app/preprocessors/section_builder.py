from __future__ import annotations

import hashlib
import re

from app.loaders.base_loader import LoadedDocument


class SectionBuilder:
    """Split headings outside protected HTML blocks; offsets are parsed-text offsets."""

    def split(self, loaded: LoadedDocument):
        if loaded.metadata.get('category') == 'Case':
            return [loaded]
        protected = list(re.finditer(r'<(table|figure)\b[^>]*>.*?</\1>', loaded.content, re.S | re.I))
        headings = [m for m in re.finditer(r'^#{1,6}\s+(.+)$', loaded.content, re.M)
                    if not any(p.start() <= m.start() < p.end() for p in protected)]
        starts = sorted({0, *(m.start() for m in headings), len(loaded.content)})
        result = []
        for start, end in zip(starts, starts[1:]):
            text = loaded.content[start:end].strip()
            if not text:
                continue
            heading = next((m.group(1) for m in headings if m.start() == start), loaded.metadata.get('section', loaded.source))
            result.append(LoadedDocument(text, loaded.source, {**loaded.metadata, 'section': heading,
                'section_source_offset': start, 'section_offset_scope': 'parsed_text'}))
        return result


def section_id(doc_id, revision_id, metadata):
    key = '\0'.join(map(str, [doc_id, revision_id, metadata.get('section', ''), metadata.get('page', ''), metadata.get('section_index', 0)]))
    return 'sec_' + hashlib.sha256(key.encode()).hexdigest()[:24]


def section_records(document, chunks):
    groups = {}
    for chunk in chunks:
        metadata = chunk['metadata']
        sid = metadata.get('section_id') or section_id(document['document_id'], document['revision_id'], metadata)
        metadata['section_id'] = sid
        record = groups.setdefault(sid, {'section_id': sid, 'document_id': document['document_id'],
            'revision_id': document['revision_id'], 'title': metadata.get('section', document.get('title', '正文')),
            'page': metadata.get('page'), 'parser': metadata.get('parser', 'text'),
            'parse_route': metadata.get('parse_route', 'native'), 'chunk_ids': []})
        record['chunk_ids'].append(chunk['chunk_id'])
    return list(groups.values())
