"""Durable local work queue. Claims, progress and current-vector publication are transactional."""
import json
import os
from uuid import uuid4
from app.storage.knowledge_store import KnowledgeConflictError,digest,now


ACTIVE=('running','pause_requested','cancel_requested')


class TaskStore:
    def __init__(self,store):
        self.store=store
        with store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS background_tasks (
                    task_id TEXT PRIMARY KEY,dedupe_key TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,
                    owner_token TEXT,owner_pid INTEGER,heartbeat TEXT);
                CREATE INDEX IF NOT EXISTS background_task_dedupe ON background_tasks(dedupe_key);
                CREATE TABLE IF NOT EXISTS background_items (
                    task_id TEXT NOT NULL,ordinal INTEGER NOT NULL,item_key TEXT NOT NULL,status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,payload TEXT NOT NULL,error TEXT,output TEXT,
                    PRIMARY KEY(task_id,ordinal),UNIQUE(task_id,item_key),
                    FOREIGN KEY(task_id) REFERENCES background_tasks(task_id));
                CREATE INDEX IF NOT EXISTS background_item_status ON background_items(task_id,status,ordinal);
                CREATE TABLE IF NOT EXISTS background_events (
                    event_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL);
            ''')

    def event(self,db,task_id,kind,**values):
        db.execute('INSERT INTO background_events VALUES (?,?,?,?)',('task_evt_'+uuid4().hex,task_id,now(),self.store.encode({'type':kind,**values})))

    def create(self,kind,config,items):
        key=digest(self.store.encode({'kind':kind,'config':config,'items':items}).encode())
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute("SELECT task_id FROM background_tasks WHERE dedupe_key=? AND status!='cancelled' ORDER BY rowid DESC LIMIT 1",(key,)).fetchone()
            if old:identity=old[0];reused=True
            else:
                identity='task_'+uuid4().hex;reused=False
                payload={'task_id':identity,'kind':kind,'config':config,'created_at':now(),'updated_at':now(),
                         'total':len(items),'local_only':True,'resume_policy':'local_at_least_once_cached','error':None}
                db.execute('INSERT INTO background_tasks VALUES (?,?,?,?,NULL,NULL,NULL)',(identity,key,'queued',self.store.encode(payload)))
                db.executemany('INSERT INTO background_items VALUES (?,?,?,\'pending\',0,?,NULL,NULL)',[(identity,i,item['key'],self.store.encode(item)) for i,item in enumerate(items)])
                self.event(db,identity,'created',total=len(items))
        return {**self.get(identity),'reused':reused}

    def _view(self,db,row):
        value=json.loads(row['payload']);counts={r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM background_items WHERE task_id=? GROUP BY status',(row['task_id'],))}
        outputs=db.execute("SELECT coalesce(sum(json_extract(output,'$.cache_hit')),0),coalesce(sum(json_extract(output,'$.model_call')),0) FROM background_items WHERE task_id=?",(row['task_id'],)).fetchone()
        value.update(status=row['status'],counts=counts,processed=sum(counts.get(k,0) for k in ('completed','failed','stale')),
                     cache_hits=outputs[0],embedded_items=outputs[1],heartbeat=row['heartbeat'])
        return value

    def get(self,identity):
        with self.store.connection() as db:
            db.execute('BEGIN');row=db.execute('SELECT * FROM background_tasks WHERE task_id=?',(identity,)).fetchone()
            if not row:raise FileNotFoundError('Task not found')
            return self._view(db,row)

    def page(self,page=1,page_size=20):
        with self.store.connection() as db:
            db.execute('BEGIN');total=db.execute('SELECT count(*) FROM background_tasks').fetchone()[0]
            items=[self._view(db,r) for r in db.execute('SELECT * FROM background_tasks ORDER BY rowid DESC LIMIT ? OFFSET ?',(page_size,(page-1)*page_size)).fetchall()]
            return {'items':items,'total':total,'page':page,'page_size':page_size}

    def items(self,identity,page=1,page_size=50,status=None):
        self.get(identity)
        with self.store.connection() as db:
            db.execute('BEGIN');where='task_id=?';args=[identity]
            if status:where+=' AND status=?';args.append(status)
            total=db.execute('SELECT count(*) FROM background_items WHERE '+where,args).fetchone()[0]
            rows=db.execute('SELECT * FROM background_items WHERE '+where+' ORDER BY ordinal LIMIT ? OFFSET ?',[*args,page_size,(page-1)*page_size]).fetchall()
            return {'items':[{**json.loads(r['payload']),'ordinal':r['ordinal'],'status':r['status'],'attempts':r['attempts'],
                              'error':r['error'],'output':json.loads(r['output']) if r['output'] else None} for r in rows],
                    'total':total,'page':page,'page_size':page_size}

    def control(self,identity,action):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM background_tasks WHERE task_id=?',(identity,)).fetchone()
            if not row:raise FileNotFoundError('Task not found')
            state=row['status'];payload=json.loads(row['payload'])
            if action=='pause' and state in ('queued','running'):target='pause_requested' if state=='running' else 'paused'
            elif action=='cancel' and state in ('queued','running','pause_requested','paused','failed','partial'):target='cancel_requested' if state in ACTIVE else 'cancelled'
            elif action=='resume' and state=='paused':target='queued'
            elif action=='retry' and state in ('failed','partial'):
                target='queued';db.execute("UPDATE background_items SET status='pending',error=NULL WHERE task_id=? AND status='failed'",(identity,))
            else:raise KnowledgeConflictError('Action is not valid for current task state')
            payload.update(updated_at=now(),error=None)
            db.execute('UPDATE background_tasks SET status=?,payload=? WHERE task_id=?',(target,self.store.encode(payload),identity))
            self.event(db,identity,'control',action=action,previous=state,status=target)
        return self.get(identity)

    def recover(self,db):
        for row in db.execute("SELECT * FROM background_tasks WHERE status IN ('running','pause_requested','cancel_requested')").fetchall():
            if self.store._owner_is_alive(row['owner_pid']):continue
            target={'pause_requested':'paused','cancel_requested':'cancelled'}.get(row['status'],'queued')
            db.execute("UPDATE background_items SET status='pending' WHERE task_id=? AND status='running'",(row['task_id'],))
            db.execute('UPDATE background_tasks SET status=?,owner_token=NULL,owner_pid=NULL WHERE task_id=?',(target,row['task_id']))
            self.event(db,row['task_id'],'recovered_after_process_exit',status=target)

    def claim(self,token):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.recover(db)
            if db.execute("SELECT 1 FROM background_tasks WHERE status IN ('running','pause_requested','cancel_requested') LIMIT 1").fetchone():return None
            row=db.execute("SELECT task_id,payload FROM background_tasks WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not row:return None
            db.execute("UPDATE background_tasks SET status='running',owner_token=?,owner_pid=?,heartbeat=? WHERE task_id=?",(token,os.getpid(),now(),row[0]))
            self.event(db,row[0],'claimed')
            return json.loads(row[1])

    def owned(self,db,identity,token):
        row=db.execute('SELECT * FROM background_tasks WHERE task_id=? AND owner_token=?',(identity,token)).fetchone()
        if not row or row['status'] not in ACTIVE:raise KnowledgeConflictError('Worker no longer owns this task')
        return row

    def take(self,identity,token,limit=8):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');row=self.owned(db,identity,token)
            if row['status']!='running':return []
            rows=db.execute("SELECT ordinal,payload FROM background_items WHERE task_id=? AND status='pending' ORDER BY ordinal LIMIT ?",(identity,limit)).fetchall()
            items=[];chars=0
            for ordinal,raw in rows:
                value=json.loads(raw);length=value.get('characters',0)
                if items and chars+length>12000:break
                items.append({**value,'ordinal':ordinal});chars+=length
            db.executemany("UPDATE background_items SET status='running',attempts=attempts+1 WHERE task_id=? AND ordinal=?",[(identity,item['ordinal']) for item in items])
            db.execute('UPDATE background_tasks SET heartbeat=? WHERE task_id=?',(now(),identity))
            return items

    def publish(self,identity,token,items,model=None):
        """Publish only vectors whose source revision/content is still current."""
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');self.owned(db,identity,token)
            for item in items:
                state=item.get('status','completed');error=item.get('error');output=item.get('output',{})
                if 'vector' in item:
                    # Content-addressed cache may survive source changes; current replicas may not.
                    db.execute('INSERT OR IGNORE INTO vectors VALUES (?,?)',(item['cache_key'],self.store.encode(item['vector'])))
                    current=db.execute('SELECT c.payload FROM chunks c JOIN documents d ON d.document_id=c.document_id WHERE c.chunk_id=? AND d.deleted=0',(item['key'],)).fetchone()
                    chunk=json.loads(current[0]) if current else None
                    if (not chunk or chunk['metadata'].get('revision_id')!=item['revision_id'] or digest(chunk['content'].encode())!=item['content_sha256']
                            or not chunk['metadata'].get('enabled',True) or not chunk['metadata'].get('retrievable',True)):
                        state='stale';error='Source revision changed; create a new index task for current content'
                    else:
                        vector_id='vec_'+digest((model['fingerprint']+'\0'+chunk['chunk_id']+'\0'+str(item['revision_id'])+'\0'+chunk['content']).encode())
                        value={'vector_id':vector_id,'chunk_id':chunk['chunk_id'],'document_id':chunk['doc_id'],'revision_id':item['revision_id'],
                               'fingerprint':model['fingerprint'],'model':model['model'],'dimension':model['dimension'],'backend':'sqlite_exact','status':'ready','updated_at':now()}
                        db.execute('INSERT INTO vector_records VALUES (?,?,?,?,?,?) ON CONFLICT(vector_id) DO UPDATE SET payload=excluded.payload',
                            (vector_id,chunk['chunk_id'],chunk['doc_id'],item['revision_id'],model['fingerprint'],self.store.encode(value)))
                db.execute('UPDATE background_items SET status=?,error=?,output=? WHERE task_id=? AND ordinal=?',(state,error,self.store.encode(output),identity,item['ordinal']))
            db.execute('UPDATE background_tasks SET heartbeat=? WHERE task_id=?',(now(),identity))

    def release(self,identity,token,failure=None,stopping=False):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE');row=self.owned(db,identity,token);payload=json.loads(row['payload'])
            counts={r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM background_items WHERE task_id=? GROUP BY status',(identity,))}
            state=row['status']
            if state=='pause_requested':target='paused'
            elif state=='cancel_requested':target='cancelled'
            elif failure:target='failed'
            elif stopping:target='queued'
            elif counts.get('pending',0)+counts.get('running',0):return False
            else:target='partial' if counts.get('failed',0)+counts.get('stale',0) else 'completed'
            db.execute("UPDATE background_items SET status='pending' WHERE task_id=? AND status='running'",(identity,))
            payload.update(updated_at=now(),error=failure)
            db.execute('UPDATE background_tasks SET status=?,payload=?,owner_token=NULL,owner_pid=NULL,heartbeat=? WHERE task_id=?',(target,self.store.encode(payload),now(),identity))
            self.event(db,identity,'released',status=target,error=failure)
            return True
