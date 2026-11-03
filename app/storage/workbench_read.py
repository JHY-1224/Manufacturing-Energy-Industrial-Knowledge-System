"""Bounded SQL reads for the workbench; legacy full-list contracts remain available."""
import json


class WorkbenchReadStore:
    def __init__(self, store):
        self.store=store
        with store.connection() as db:
            db.executescript('''
                CREATE INDEX IF NOT EXISTS documents_base ON documents(deleted,json_extract(payload,'$.knowledge_base_id'));
                CREATE INDEX IF NOT EXISTS vector_records_chunk ON vector_records(chunk_id);
                CREATE INDEX IF NOT EXISTS chunks_base ON chunks(json_extract(payload,'$.metadata.knowledge_base_id'));
            ''')

    @staticmethod
    def bounds(page,page_size):
        if not 1<=page or not 1<=page_size<=100:raise ValueError('Invalid page or page_size')

    def documents(self,page=1,page_size=50,knowledge_base_id=None,q='',compact=False,category=None,review_state=None):
        self.bounds(page,page_size);where=['deleted=0'];args=[]
        if knowledge_base_id:where.append("json_extract(payload,'$.knowledge_base_id')=?");args.append(knowledge_base_id)
        if q:
            where.append("(instr(lower(coalesce(json_extract(payload,'$.source_name'),'')||' '||coalesce(json_extract(payload,'$.source_caption'),'')||' '||coalesce(json_extract(payload,'$.assigned_category'),'')),lower(?))>0 OR EXISTS (SELECT 1 FROM chunks c WHERE c.document_id=documents.document_id AND json_extract(c.payload,'$.metadata.chunk_kind')='source_caption' AND instr(lower(json_extract(c.payload,'$.content')),lower(?))>0))")
            args.extend([q,q])
        if category:where.append("json_extract(payload,'$.assigned_category')=?");args.append(category)
        if review_state:
            where.append("coalesce((SELECT CASE WHEN r.state='withdrawn' THEN 'withdrawn' WHEN r.revision_id!=coalesce(json_extract(documents.payload,'$.revision_id'),'') THEN 'needs_review' ELSE r.state END FROM knowledge_reviews r WHERE r.document_id=documents.document_id),'unreviewed')=?")
            args.append(review_state)
        clause=' AND '.join(where)
        with self.store.connection() as db:
            db.execute('BEGIN')
            total=db.execute('SELECT count(*) FROM documents WHERE '+clause,args).fetchone()[0]
            items=[json.loads(r[0]) for r in db.execute('SELECT payload FROM documents WHERE '+clause+' ORDER BY rowid DESC LIMIT ? OFFSET ?',[*args,page_size,(page-1)*page_size])]
            if items:
                from app.storage.review_store import effective_state
                ids=[d['document_id'] for d in items]
                reviews={r['document_id']:dict(r) for r in db.execute('SELECT document_id,revision_id,state,version FROM knowledge_reviews WHERE document_id IN ('+','.join('?' for _ in ids)+')',ids)}
                for d in items:
                    review=reviews.get(d['document_id'])
                    d['knowledge_review_state']=effective_state(review,d.get('revision_id'))
            if items and not compact:
                ids=[d['document_id'] for d in items]
                ready={r[0]:r[1] for r in db.execute("SELECT c.document_id,count(DISTINCT c.chunk_id) FROM chunks c JOIN vector_records v ON v.chunk_id=c.chunk_id AND v.revision_id=json_extract(c.payload,'$.metadata.revision_id') WHERE c.document_id IN ("+','.join('?' for _ in ids)+") AND json_extract(v.payload,'$.model')!='offline-hash-sha256-v1' AND coalesce(json_extract(c.payload,'$.metadata.enabled'),1)=1 AND coalesce(json_extract(c.payload,'$.metadata.retrievable'),1)=1 GROUP BY c.document_id",ids)}
                active={r[0]:r[1] for r in db.execute("SELECT document_id,count(*) FROM chunks WHERE document_id IN ("+','.join('?' for _ in ids)+") AND coalesce(json_extract(payload,'$.metadata.enabled'),1)=1 AND coalesce(json_extract(payload,'$.metadata.retrievable'),1)=1 GROUP BY document_id",ids)}
                for d in items:
                    count=ready.get(d['document_id'],0);expected=active.get(d['document_id'],0)
                    d.update(text_embedding_count=count,active_chunk_count=expected,
                             embedding_status='ready' if count==expected and expected else 'partial' if count else 'pending')
        if compact:items=[{k:d.get(k) for k in ['document_id','source_name','title','knowledge_base_id','status','knowledge_review_state']} for d in items]
        return {'items':items,'total':total,'page':page,'page_size':page_size,'has_more':page*page_size<total}

    def chunks(self,page=1,page_size=50,document_id=None,q='',knowledge_base_id=None,chunk_id=None):
        self.bounds(page,page_size);where=['d.deleted=0'];args=[]
        for field,value in [('c.document_id',document_id),("json_extract(c.payload,'$.metadata.knowledge_base_id')",knowledge_base_id),('c.chunk_id',chunk_id)]:
            if value:where.append(field+'=?');args.append(value)
        if q:where.append("instr(lower(json_extract(c.payload,'$.content')||' '||coalesce(json_extract(c.payload,'$.metadata.section'),'')),lower(?))>0");args.append(q)
        clause=' AND '.join(where);source=' FROM chunks c JOIN documents d ON d.document_id=c.document_id WHERE '+clause
        with self.store.connection() as db:
            db.execute('BEGIN')
            total=db.execute('SELECT count(*)'+source,args).fetchone()[0]
            items=[json.loads(r[0]) for r in db.execute('SELECT c.payload'+source+' ORDER BY c.rowid LIMIT ? OFFSET ?',[*args,page_size,(page-1)*page_size])]
            vectors={c['chunk_id']:[] for c in items}
            if vectors:
                for row in db.execute('SELECT chunk_id,payload FROM vector_records WHERE chunk_id IN ('+','.join('?' for _ in vectors)+') ORDER BY rowid DESC',list(vectors)):
                    vectors[row[0]].append(json.loads(row[1]))
            for c in items:c.update(enabled=c['metadata'].get('enabled',True),vector_records=vectors[c['chunk_id']],token_count=len(c['content'])//2,token_count_kind='character_estimate')
        return {'items':items,'total':total,'page':page,'page_size':page_size,'has_more':page*page_size<total}

    def counts(self):
        with self.store.connection() as db:
            db.execute('BEGIN')
            return {'documents':db.execute('SELECT count(*) FROM documents WHERE deleted=0').fetchone()[0],
                    'chunks':db.execute('SELECT count(*) FROM chunks c JOIN documents d ON d.document_id=c.document_id WHERE d.deleted=0').fetchone()[0],
                    'demo':db.execute("SELECT count(*) FROM documents WHERE deleted=0 AND json_extract(payload,'$.data_source')='demo'").fetchone()[0],
                    'images':db.execute("SELECT count(*) FROM documents WHERE deleted=0 AND json_extract(payload,'$.image_sha256') IS NOT NULL").fetchone()[0],
                    'analyzed_images':db.execute("SELECT count(*) FROM documents WHERE deleted=0 AND json_extract(payload,'$.image_analysis_key') IS NOT NULL").fetchone()[0],
                    'ocr_with_text':db.execute("SELECT count(*) FROM documents WHERE deleted=0 AND json_extract(payload,'$.ocr_status')='completed'").fetchone()[0],
                    'categories':[{'knowledge_base_id':r[0],'name':r[1],'count':r[2]} for r in db.execute("SELECT json_extract(payload,'$.knowledge_base_id'),json_extract(payload,'$.assigned_category'),count(*) FROM documents WHERE deleted=0 AND json_extract(payload,'$.assigned_category') IS NOT NULL GROUP BY 1,2 ORDER BY 1,2")],
                    'bases':{r[0]:r[1] for r in db.execute("SELECT coalesce(json_extract(payload,'$.knowledge_base_id'),'energy'),count(*) FROM documents WHERE deleted=0 GROUP BY 1")}}
