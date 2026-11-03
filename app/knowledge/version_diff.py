"""Read-only, document-scoped comparisons of immutable processing revisions."""
from collections import defaultdict,deque
import difflib,json
from app.storage.knowledge_store import digest

class RevisionDiff:
    def __init__(self,store):self.store=store

    def compare(self,document_id,from_revision=None,to_revision=None,page=1,page_size=20):
        if page<1 or not 1<=page_size<=50:raise ValueError('Invalid comparison page')
        with self.store.connection() as db:
            db.execute('BEGIN')
            row=db.execute('SELECT payload FROM documents WHERE document_id=? AND deleted=0',(document_id,)).fetchone()
            if not row:raise FileNotFoundError('Document not found')
            document=json.loads(row[0])
            revisions=db.execute('SELECT revision_id,created_at FROM revisions WHERE document_id=? ORDER BY rowid DESC',(document_id,)).fetchall()
            ids=[r[0] for r in revisions];to_revision=to_revision or document.get('revision_id')
            if to_revision not in ids:raise FileNotFoundError('Target revision not found for this document')
            if from_revision is None:
                at=ids.index(to_revision);from_revision=ids[at+1] if at+1<len(ids) else to_revision
            if from_revision not in ids:raise FileNotFoundError('Source revision not found for this document')
            snapshots={}
            for identity in {from_revision,to_revision}:
                row=db.execute('SELECT payload FROM revisions WHERE revision_id=? AND document_id=?',(identity,document_id)).fetchone()
                snapshots[identity]=json.loads(row[0])
        before=snapshots[from_revision].get('chunks',[]);after=snapshots[to_revision].get('chunks',[])
        by_id={c['chunk_id']:i for i,c in enumerate(after)};used=set();pairs=[];unpaired=[]
        for c in before:
            i=by_id.get(c['chunk_id'])
            if i is not None and i not in used:pairs.append((c,after[i]));used.add(i)
            else:unpaired.append(c)
        # Re-chunking may change IDs. Match identical text with multiplicity intact.
        by_hash=defaultdict(deque)
        for i,c in enumerate(after):
            if i not in used:by_hash[digest(c['content'].encode())].append(i)
        for c in unpaired:
            matches=by_hash[digest(c['content'].encode())]
            if matches:
                i=matches.popleft();used.add(i);pairs.append((c,after[i]))
            else:pairs.append((c,None))
        pairs.extend((None,c) for i,c in enumerate(after) if i not in used)
        keys=('enabled','retrievable','page','section','chunk_kind','table_id','parent_id')
        counts=dict(added=0,removed=0,modified=0,unchanged=0);changes=[]
        def view(c):
            if c is None:return None
            text=c['content'];return {'chunk_id':c['chunk_id'],'content':text[:8000],'content_truncated':len(text)>8000,'content_sha256':digest(text.encode()),'metadata':{k:c.get('metadata',{}).get(k) for k in keys}}
        for a,b in pairs:
            kind='added' if a is None else 'removed' if b is None else 'modified' if a['content']!=b['content'] or any(a.get('metadata',{}).get(k)!=b.get('metadata',{}).get(k) for k in keys) else 'unchanged'
            counts[kind]+=1
            if kind!='unchanged':
                lines=list(difflib.unified_diff((a or {}).get('content','').splitlines(),(b or {}).get('content','').splitlines(),fromfile=from_revision,tofile=to_revision,lineterm=''))
                delta='\n'.join(lines)
                changes.append({'kind':kind,'before':view(a),'after':view(b),'diff':delta[:12000],'diff_truncated':len(delta)>12000})
        start=(page-1)*page_size
        return {'document_id':document_id,'current_revision_id':document.get('revision_id'),'from_revision':from_revision,'to_revision':to_revision,'counts':counts,'items':changes[start:start+page_size],'total':len(changes),'page':page,'page_size':page_size,'single_revision':len(ids)==1,'same_revision':from_revision==to_revision,'read_only':True,'source_originals_changed':False}
