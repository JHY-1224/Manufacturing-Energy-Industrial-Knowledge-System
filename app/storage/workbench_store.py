from __future__ import annotations

import json
from uuid import uuid4

from app.storage.knowledge_store import KnowledgeConflictError, now


def initialize_workbench(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS sections (section_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, revision_id TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS sections_document ON sections(document_id);
        CREATE TABLE IF NOT EXISTS vector_records (vector_id TEXT PRIMARY KEY, chunk_id TEXT NOT NULL, document_id TEXT NOT NULL, revision_id TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS vectors_document ON vector_records(document_id);
        CREATE INDEX IF NOT EXISTS vectors_chunk_revision ON vector_records(chunk_id,revision_id);
        CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, payload TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS sessions_recent ON sessions(archived,json_extract(payload,'$.updated_at') DESC,session_id);
        CREATE TABLE IF NOT EXISTS messages (message_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, request_id TEXT NOT NULL, role TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(session_id,request_id,role));
        CREATE TABLE IF NOT EXISTS agent_events (event_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, request_id TEXT NOT NULL, payload TEXT NOT NULL);
    ''')
    from app.storage.review_store import initialize_reviews
    initialize_reviews(db)


class WorkbenchStore:
    """Uses the same transaction and version authority as the P1 knowledge store."""

    def __init__(self, store):
        self.store = store

    def sections(self, document_id=None):
        with self.store.connection() as db:
            rows = db.execute('SELECT payload FROM sections' + (' WHERE document_id=?' if document_id else '') + ' ORDER BY rowid', (document_id,) if document_id else ())
            return [json.loads(row[0]) for row in rows]

    def document_snapshot(self, document_id):
        with self.store.connection() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT payload FROM documents WHERE document_id=? AND deleted=0', (document_id,)).fetchone()
            if not row:
                raise FileNotFoundError('Document not found')
            chunks = [json.loads(r[0]) for r in db.execute('SELECT payload FROM chunks WHERE document_id=? ORDER BY rowid', (document_id,))]
            return json.loads(row[0]), chunks

    def vector_records(self, chunk_id):
        with self.store.connection() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM vector_records WHERE chunk_id=? ORDER BY rowid DESC', (chunk_id,))]

    def assert_sources_current(self, hits):
        """An unrelated ingestion must not invalidate an evidence-bearing answer."""
        with self.store.connection() as db:
            db.execute('BEGIN')
            for hit in hits:
                review = db.execute('SELECT state,version FROM knowledge_reviews WHERE document_id=?', (hit.doc_id,)).fetchone()
                version = review['version'] if review else 0
                if ((review and review['state'] == 'withdrawn') or
                        ('knowledge_review_version' in hit.metadata and version != hit.metadata['knowledge_review_version'])):
                    raise KnowledgeConflictError('Source review changed; retrieve the current available evidence')
                identities=set(hit.metadata.get('evidence_chunk_ids',[]))
                identities.add(hit.metadata.get('source_chunk_id',hit.chunk_id))
                for identity in identities:
                    row=db.execute('SELECT c.payload FROM chunks c JOIN documents d ON d.document_id=c.document_id WHERE c.chunk_id=? AND d.deleted=0',(identity,)).fetchone()
                    chunk=json.loads(row[0]) if row else None
                    if (not chunk or chunk['doc_id']!=hit.doc_id or chunk['metadata'].get('revision_id')!=hit.metadata.get('revision_id')
                        or not chunk['metadata'].get('enabled',True) or not chunk['metadata'].get('retrievable',True)):
                        raise KnowledgeConflictError('Retrieved source changed; retry with its current revision')
            return int(db.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0])

    def record_vectors(self, chunks, fingerprint, model, dimension, backend, *, strict=True):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for chunk in chunks:
                current = db.execute('SELECT payload FROM chunks WHERE chunk_id=?', (chunk.chunk_id,)).fetchone()
                stored=json.loads(current[0]) if current else {}
                if not current or stored['content'] != chunk.content or stored['metadata'].get('revision_id') != chunk.metadata.get('revision_id'):
                    if not strict:continue
                    raise KnowledgeConflictError('Index input changed; rebuild against current revision')
                import hashlib
                vector_id = 'vec_' + hashlib.sha256((fingerprint+'\0'+chunk.chunk_id+'\0'+str(chunk.metadata.get('revision_id',''))+'\0'+chunk.content).encode()).hexdigest()
                value = {'vector_id':vector_id, 'chunk_id':chunk.chunk_id, 'document_id':chunk.doc_id,
                         'revision_id':chunk.metadata.get('revision_id',''), 'fingerprint':fingerprint,
                         'model':model, 'dimension':dimension, 'backend':backend, 'status':'ready', 'updated_at':now()}
                db.execute('INSERT INTO vector_records VALUES (?,?,?,?,?,?) ON CONFLICT(vector_id) DO UPDATE SET payload=excluded.payload', (vector_id, chunk.chunk_id, chunk.doc_id, value['revision_id'], fingerprint, self.store.encode(value)))

    def create_session(self, knowledge_base_id, title='新会话'):
        value = {'session_id':'session_'+uuid4().hex, 'knowledge_base_id':knowledge_base_id,
                 'title':title, 'created_at':now(), 'updated_at':now(), 'message_count':0, 'preview':'', 'active_request':None}
        with self.store.connection() as db:
            db.execute('INSERT INTO sessions VALUES (?,?,0)', (value['session_id'],self.store.encode(value)))
        return value

    def sessions(self):
        with self.store.connection() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM sessions WHERE archived=0 ORDER BY json_extract(payload,'$.updated_at') DESC,session_id")]

    def sessions_page(self,page=1,page_size=30,knowledge_base_id=None):
        from app.storage.workbench_read import WorkbenchReadStore
        WorkbenchReadStore.bounds(page,page_size)
        where='archived=0';args=[]
        if knowledge_base_id:
            where+=" AND json_extract(payload,'$.knowledge_base_id')=?";args.append(knowledge_base_id)
        with self.store.connection() as db:
            db.execute('BEGIN')
            total=db.execute('SELECT count(*) FROM sessions WHERE '+where,args).fetchone()[0]
            items=[json.loads(r[0]) for r in db.execute("SELECT payload FROM sessions WHERE "+where+" ORDER BY json_extract(payload,'$.updated_at') DESC,session_id LIMIT ? OFFSET ?",[*args,page_size,(page-1)*page_size])]
        return {'items':items,'total':total,'page':page,'page_size':page_size,'has_more':page*page_size<total}

    def recover_interrupted(self, db):
        for row in db.execute("SELECT payload FROM sessions WHERE json_extract(payload,'$.active_request') IS NOT NULL").fetchall():
            session=json.loads(row[0]);active=session.get('active_request')
            if active and not self.store._owner_is_alive(session.get('owner_pid')):
                message=db.execute("SELECT payload FROM messages WHERE session_id=? AND request_id=? AND role='assistant'",(session['session_id'],active)).fetchone()
                partial=json.loads(message[0]).get('content','') if message else ''
                self._finish_turn(db,session,active,partial,'interrupted',None,[],'Previous process exited; no generation was retried')

    def session(self, session_id):
        with self.store.connection() as db:
            row=db.execute('SELECT payload FROM sessions WHERE session_id=? AND archived=0',(session_id,)).fetchone()
            if not row: raise FileNotFoundError('Session not found')
            value=json.loads(row[0])
            value['messages']=[json.loads(r[0]) for r in db.execute('SELECT payload FROM messages WHERE session_id=? ORDER BY rowid',(session_id,))]
            value['events']=[json.loads(r[0]) for r in db.execute('SELECT payload FROM agent_events WHERE session_id=? ORDER BY rowid',(session_id,))]
            return value

    def begin_turn(self, session_id, request_id, question):
        import os
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM sessions WHERE session_id=? AND archived=0',(session_id,)).fetchone()
            if not row: raise FileNotFoundError('Session not found')
            session=json.loads(row[0])
            existing=db.execute('SELECT payload FROM messages WHERE session_id=? AND request_id=? AND role=?',(session_id,request_id,'assistant')).fetchone()
            if existing: raise KnowledgeConflictError('This request already exists; reload the saved turn instead of resending')
            active=session.get('active_request')
            if active and self.store._owner_is_alive(session.get('owner_pid')):
                raise KnowledgeConflictError('A response is already running in this session')
            if active:
                self._finish_turn(db,session,active,'','interrupted',None,[],'Previous process exited')
            for role,content,status in [('user',question,'completed'),('assistant','','running')]:
                message={'message_id':'msg_'+uuid4().hex,'session_id':session_id,'request_id':request_id,
                         'role':role,'content':content,'status':status,'created_at':now(),'sources':[]}
                db.execute('INSERT INTO messages VALUES (?,?,?,?,?)',(message['message_id'],session_id,request_id,role,self.store.encode(message)))
            session.update(active_request=request_id,owner_pid=os.getpid(),updated_at=now(),title=question[:40] if session['message_count']==0 else session['title'],message_count=session['message_count']+2)
            db.execute('UPDATE sessions SET payload=? WHERE session_id=?',(self.store.encode(session),session_id))

    def append_event(self, session_id, request_id, event):
        value={'event_id':'evt_'+uuid4().hex, 'created_at':now(), **event}
        with self.store.connection() as db:
            db.execute('INSERT INTO agent_events VALUES (?,?,?,?)',(value['event_id'],session_id,request_id,self.store.encode(value)))
        return value

    def _finish_turn(self,db,session,request_id,answer,status,trace_id,sources,error=None):
        row=db.execute('SELECT payload FROM messages WHERE session_id=? AND request_id=? AND role=?',(session['session_id'],request_id,'assistant')).fetchone()
        if not row:return
        message={**json.loads(row[0]),'content':answer,'status':status,'trace_id':trace_id,'sources':sources,'error':error,'updated_at':now()}
        db.execute('UPDATE messages SET payload=? WHERE message_id=?',(self.store.encode(message),message['message_id']))
        session.update(active_request=None,updated_at=now(),preview=answer[:160] if answer else (error or status))
        db.execute('UPDATE sessions SET payload=? WHERE session_id=?',(self.store.encode(session),session['session_id']))

    def finish_turn(self,session_id,request_id,answer,status,trace_id=None,sources=None,error=None):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM sessions WHERE session_id=?',(session_id,)).fetchone()
            if not row:return
            session=json.loads(row[0])
            if session.get('active_request')!=request_id:return
            self._finish_turn(db,session,request_id,answer,status,trace_id,sources or [],error)

    def archive_session(self,session_id):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM sessions WHERE session_id=? AND archived=0',(session_id,)).fetchone()
            if not row: raise FileNotFoundError('Session not found')
            if json.loads(row[0]).get('active_request'): raise KnowledgeConflictError('Wait for the current response before archiving')
            db.execute('UPDATE sessions SET archived=1 WHERE session_id=?',(session_id,))
