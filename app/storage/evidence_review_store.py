"""Append-only AI evidence reviews, separate from whole-document publication."""
import json
from uuid import uuid4
from app.storage.knowledge_store import now,KnowledgeConflictError,digest
_UNSPECIFIED=object()
class EvidenceReviewStore:
 def __init__(self,store):
  self.store=store
  with store.connection() as db:
   db.execute('CREATE TABLE IF NOT EXISTS evidence_reviews (review_id TEXT PRIMARY KEY,document_id TEXT NOT NULL,revision_id TEXT NOT NULL,payload TEXT NOT NULL)')
   db.execute('CREATE INDEX IF NOT EXISTS evidence_review_document_revision ON evidence_reviews(document_id,revision_id)')
 def append(self,document_id,revision_id,decision,scope,note,evidence,reviewer='Codex AI',*,expected_write_version=None,expected_latest_review_id=_UNSPECIFIED,review_kind='ai_source_review'):
  if decision not in {'accepted','corrected','rejected','insufficient_evidence'} or not scope or not note or not evidence:raise ValueError('Explicit evidence review required')
  if review_kind not in {'ai_source_review','user_source_review'}:raise ValueError('Unknown evidence review kind')
  with self.store.connection() as db:
   db.execute('BEGIN IMMEDIATE');r=db.execute('SELECT payload FROM documents WHERE document_id=? AND deleted=0',(document_id,)).fetchone()
   if not r or json.loads(r[0]).get('revision_id')!=revision_id:raise KnowledgeConflictError('Reviewed revision changed')
   document=json.loads(r[0])
   if expected_write_version is not None and (document.get('write_version')!=expected_write_version or document.get('source_sha256')!=evidence.get('source_sha256')):raise KnowledgeConflictError('Reviewed source changed')
   if expected_latest_review_id is not _UNSPECIFIED:
    latest=db.execute("SELECT review_id FROM evidence_reviews WHERE document_id=? AND revision_id=? AND json_extract(payload,'$.scope')=? AND json_extract(payload,'$.evidence.fingerprint') IS ? ORDER BY rowid DESC LIMIT 1",(document_id,revision_id,scope,evidence.get('fingerprint'))).fetchone()
    if (latest[0] if latest else None)!=expected_latest_review_id:raise KnowledgeConflictError('Another source review was saved; refresh before editing')
   value={'review_id':('ai_review_' if review_kind=='ai_source_review' else 'source_review_')+uuid4().hex,'document_id':document_id,'revision_id':revision_id,'decision':decision,'scope':scope,'note':note,'evidence':evidence,'reviewer':reviewer,'review_kind':review_kind,'human_approved':False,'document_published':False,'created_at':now()}
   db.execute('INSERT INTO evidence_reviews VALUES (?,?,?,?)',(value['review_id'],document_id,revision_id,self.store.encode(value)))
   self.store.bump(db)
  return value
 def current(self,document_id):
  document=self.store.get_document(document_id)
  if not document:raise FileNotFoundError('Document not found')
  with self.store.connection() as db:items=[json.loads(r[0]) for r in db.execute('SELECT payload FROM evidence_reviews WHERE document_id=? AND revision_id=? ORDER BY rowid DESC',(document_id,document.get('revision_id')))]
  return {'items':items,'document_id':document_id,'revision_id':document.get('revision_id'),'human_approved':False,'document_publication_is_separate':True}
