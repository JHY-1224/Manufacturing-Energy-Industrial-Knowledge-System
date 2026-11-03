"""Revision-bound, explicit user reviews; source provenance remains immutable."""
import json
from uuid import uuid4

from app.storage.knowledge_store import KnowledgeConflictError, now


def initialize_reviews(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS knowledge_reviews (
            document_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL,
            state TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS knowledge_review_events (
            event_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
            created_at TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS review_events_document ON knowledge_review_events(document_id);
    ''')


def effective_state(review, revision_id):
    if not review:
        return 'unreviewed'
    # Withdrawal survives a later ingestion; editing cannot silently republish it.
    if review['state'] == 'withdrawn':
        return 'withdrawn'
    if review['revision_id'] != (revision_id or ''):
        return 'needs_review'
    return review['state']


def available(metadata, review_mode='reference'):
    state = metadata.get('knowledge_review_state', 'unreviewed')
    return (metadata.get('enabled', True) and metadata.get('retrievable', True)
            and state != 'withdrawn' and (review_mode != 'published_only' or state == 'published'))


class ReviewStore:
    def __init__(self, store):
        self.store = store

    def decorate(self, chunks):
        with self.store.connection() as db:
            reviews = {r['document_id']: dict(r) for r in db.execute(
                'SELECT document_id,revision_id,state,version,payload FROM knowledge_reviews')}
        for chunk in chunks:
            review = reviews.get(chunk.doc_id)
            chunk.metadata.update(knowledge_review_state=effective_state(review, chunk.metadata.get('revision_id')),
                                  knowledge_review_version=review['version'] if review else 0)
            if review and chunk.metadata['knowledge_review_state'] == 'published':
                chunk.metadata['knowledge_applicability'] = json.loads(review['payload'])['applicability']
        return chunks

    def _get(self, db, document_id):
        row = db.execute('SELECT payload FROM documents WHERE document_id=? AND deleted=0', (document_id,)).fetchone()
        if not row:
            raise FileNotFoundError('Document not found')
        document = json.loads(row[0])
        review = db.execute('SELECT * FROM knowledge_reviews WHERE document_id=?', (document_id,)).fetchone()
        record = json.loads(review['payload']) if review else {}
        return {'document_id': document_id, 'source_name': document.get('source_name', document.get('title')),
                'knowledge_base_id': document.get('knowledge_base_id'),
                'current_revision_id': document.get('revision_id'), 'write_version': document['write_version'],
                'state': effective_state(review, document.get('revision_id')),
                'review_version': review['version'] if review else 0, 'record': record,
                'source_review_status': document.get('review_status', document.get('provenance', {}).get('review_status', 'unreviewed')),
                'source_sha256': document.get('source_sha256'), 'source_page': document.get('source_page', ''),
                'source_version': document.get('source_version', ''), 'chunk_count': document.get('chunk_count', 0)}

    def get(self, document_id):
        with self.store.connection() as db:
            db.execute('BEGIN')
            return self._get(db, document_id)

    def transition(self, document_id, state, expected_write_version, expected_review_version,
                   reviewer, note, applicability=''):
        if state not in {'draft', 'published', 'withdrawn'}:
            raise ValueError('Unknown review state')
        if not reviewer.strip() or not note.strip():
            raise ValueError('Reviewer and review note are required')
        if state == 'published' and not applicability.strip():
            raise ValueError('Publication requires an explicit applicability statement')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            document = self.store._check_version(db, document_id, expected_write_version)
            current = self._get(db, document_id)
            if current['review_version'] != expected_review_version:
                raise KnowledgeConflictError('Review changed; reload before submitting')
            allowed = {'unreviewed': {'draft', 'withdrawn'}, 'needs_review': {'draft', 'withdrawn'},
                       'draft': {'draft', 'published', 'withdrawn'},
                       'published': {'draft', 'withdrawn'}, 'withdrawn': {'draft'}}
            if state not in allowed[current['state']]:
                raise KnowledgeConflictError('Invalid review transition; review the current revision as a draft first')
            if state == 'published':
                if document.get('status') != 'indexed' or not db.execute(
                    "SELECT 1 FROM chunks WHERE document_id=? AND coalesce(json_extract(payload,'$.metadata.enabled'),1)=1 AND coalesce(json_extract(payload,'$.metadata.retrievable'),1)=1 LIMIT 1", (document_id,)).fetchone():
                    raise ValueError('Publication requires an indexed document with available evidence')
            record = {'document_id': document_id, 'revision_id': document.get('revision_id') or '',
                      'state': state, 'review_version': current['review_version'] + 1,
                      'reviewer': reviewer.strip(), 'note': note.strip(), 'applicability': applicability.strip(),
                      'source_sha256': document.get('source_sha256'), 'created_at': now(),
                      'review_kind': 'explicit_user_review', 'previous_state': current['state']}
            db.execute('INSERT INTO knowledge_reviews VALUES (?,?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET revision_id=excluded.revision_id,state=excluded.state,version=excluded.version,payload=excluded.payload',
                       (document_id, record['revision_id'], state, record['review_version'], self.store.encode(record)))
            db.execute('INSERT INTO knowledge_review_events VALUES (?,?,?,?)',
                       ('review_' + uuid4().hex, document_id, record['created_at'], self.store.encode(record)))
            self.store.bump(db)
            return self._get(db, document_id)

    def history(self, document_id, page=1, page_size=20):
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError('Invalid review history page')
        with self.store.connection() as db:
            db.execute('BEGIN'); self._get(db, document_id)
            total = db.execute('SELECT count(*) FROM knowledge_review_events WHERE document_id=?', (document_id,)).fetchone()[0]
            items = [json.loads(r[0]) for r in db.execute('SELECT payload FROM knowledge_review_events WHERE document_id=? ORDER BY rowid DESC LIMIT ? OFFSET ?', (document_id, page_size, (page-1)*page_size))]
            return {'items': items, 'total': total, 'page': page, 'page_size': page_size}

    def summary(self):
        with self.store.connection() as db:
            rows = db.execute("""SELECT CASE WHEN r.state='withdrawn' THEN 'withdrawn'
                WHEN r.document_id IS NULL THEN 'unreviewed'
                WHEN r.revision_id!=coalesce(json_extract(d.payload,'$.revision_id'),'') THEN 'needs_review'
                ELSE r.state END AS state,count(*) FROM documents d LEFT JOIN knowledge_reviews r
                ON r.document_id=d.document_id WHERE d.deleted=0 GROUP BY 1""")
            return {'counts': dict(rows), 'scope': 'current_documents', 'automatic_approval': False}
