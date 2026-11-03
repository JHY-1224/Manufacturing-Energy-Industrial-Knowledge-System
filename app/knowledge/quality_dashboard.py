"""SQL quality counters and one read-only troubleshooting surface."""
import importlib.util,json,os,platform,sqlite3,sys
from urllib.parse import urlparse
from urllib.request import build_opener,ProxyHandler
from app.storage.knowledge_store import now

class QualityDashboard:
    def __init__(self,store):self.store=store

    def summary(self,base=None):
        where='d.deleted=0';args=[]
        if base:where+=" AND json_extract(d.payload,'$.knowledge_base_id')=?";args.append(base)
        with self.store.connection() as db:
            db.execute('BEGIN');tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts=db.execute("SELECT count(*),sum(json_extract(d.payload,'$.image_sha256') IS NOT NULL),sum(coalesce(json_extract(d.payload,'$.source_version'),'')=''),sum(json_extract(d.payload,'$.status')='failed') FROM documents d WHERE "+where,args).fetchone()
            chunks=db.execute("SELECT count(*),sum(coalesce(json_extract(c.payload,'$.metadata.enabled'),1)=1 AND coalesce(json_extract(c.payload,'$.metadata.retrievable'),1)=1) FROM chunks c JOIN documents d ON c.document_id=d.document_id WHERE "+where,args).fetchone()
            ready=db.execute("SELECT count(DISTINCT v.chunk_id) FROM vector_records v JOIN chunks c ON v.chunk_id=c.chunk_id JOIN documents d ON d.document_id=c.document_id WHERE "+where+" AND v.revision_id=json_extract(d.payload,'$.revision_id') AND coalesce(json_extract(c.payload,'$.metadata.enabled'),1)=1 AND coalesce(json_extract(c.payload,'$.metadata.retrievable'),1)=1 AND json_extract(v.payload,'$.status')='ready'",args).fetchone()[0]
            by_base=[{'knowledge_base_id':r[0],'documents':r[1],'images':r[2]} for r in db.execute("SELECT json_extract(d.payload,'$.knowledge_base_id'),count(*),sum(json_extract(d.payload,'$.image_sha256') IS NOT NULL) FROM documents d WHERE "+where+" GROUP BY 1 ORDER BY 1",args)]
            ocr=dict(db.execute("SELECT coalesce(json_extract(d.payload,'$.ocr_status'),'not_processed'),count(*) FROM documents d WHERE "+where+" AND json_extract(d.payload,'$.image_sha256') IS NOT NULL GROUP BY 1",args))
            def derived(kind):
                if 'multimodal_assets' not in tables:return 0
                return db.execute("SELECT count(*) FROM documents d WHERE "+where+" AND json_extract(d.payload,'$.image_sha256') IS NOT NULL AND EXISTS(SELECT 1 FROM multimodal_assets a WHERE a.document_id=d.document_id AND a.revision_id=json_extract(d.payload,'$.revision_id') AND a.source_sha256=json_extract(d.payload,'$.source_sha256') AND a.kind=?)",[*args,kind]).fetchone()[0]
            reviews=[];recent=[];labels=0
            if 'evidence_reviews' in tables:
                reviews=[{'decision':r[0],'kind':r[1],'count':r[2]} for r in db.execute("SELECT json_extract(e.payload,'$.decision'),json_extract(e.payload,'$.review_kind'),count(*) FROM evidence_reviews e JOIN documents d ON e.document_id=d.document_id AND e.revision_id=json_extract(d.payload,'$.revision_id') WHERE "+where+" GROUP BY 1,2",args)]
                recent=[json.loads(r[0]) for r in db.execute("SELECT e.payload FROM evidence_reviews e JOIN documents d ON e.document_id=d.document_id AND e.revision_id=json_extract(d.payload,'$.revision_id') WHERE "+where+" ORDER BY e.rowid DESC LIMIT 12",args)]
                if 'multimodal_assets' in tables:
                    labels=db.execute("SELECT count(*) FROM documents d WHERE "+where+" AND (SELECT json_extract(e.payload,'$.decision') FROM evidence_reviews e WHERE e.document_id=d.document_id AND e.revision_id=json_extract(d.payload,'$.revision_id') AND json_extract(e.payload,'$.scope')='visual_source_labels' AND json_extract(e.payload,'$.evidence.source_sha256')=json_extract(d.payload,'$.source_sha256') AND json_extract(e.payload,'$.evidence.fingerprint')=(SELECT a.fingerprint FROM multimodal_assets a WHERE a.document_id=d.document_id AND a.revision_id=json_extract(d.payload,'$.revision_id') AND a.source_sha256=json_extract(d.payload,'$.source_sha256') AND a.kind='observation' ORDER BY a.rowid DESC LIMIT 1) ORDER BY e.rowid DESC LIMIT 1) IN ('accepted','corrected')",args).fetchone()[0]
            structures={}
            if 'structure_resolutions' in tables:structures=dict(db.execute("SELECT json_extract(s.payload,'$.disposition'),count(*) FROM structure_resolutions s JOIN documents d ON s.document_id=d.document_id AND s.revision_id=json_extract(d.payload,'$.revision_id') AND s.source_sha256=json_extract(d.payload,'$.source_sha256') WHERE "+where+" GROUP BY 1",args))
            task_states=dict(db.execute('SELECT status,count(*) FROM background_tasks GROUP BY status')) if 'background_tasks' in tables else {}
            described=derived('observation');indexed=derived('embedding')
            missing_index="EXISTS(SELECT 1 FROM chunks c WHERE c.document_id=d.document_id AND coalesce(json_extract(c.payload,'$.metadata.enabled'),1)=1 AND coalesce(json_extract(c.payload,'$.metadata.retrievable'),1)=1 AND NOT EXISTS(SELECT 1 FROM vector_records v WHERE v.chunk_id=c.chunk_id AND v.revision_id=json_extract(d.payload,'$.revision_id') AND json_extract(v.payload,'$.status')='ready'))"
            no_observation="NOT EXISTS(SELECT 1 FROM multimodal_assets a WHERE a.document_id=d.document_id AND a.revision_id=json_extract(d.payload,'$.revision_id') AND a.source_sha256=json_extract(d.payload,'$.source_sha256') AND a.kind='observation')" if 'multimodal_assets' in tables else '1'
            missing_description="json_extract(d.payload,'$.image_sha256') IS NOT NULL AND "+no_observation
            issue_rows=db.execute("SELECT d.document_id,coalesce(json_extract(d.payload,'$.source_name'),json_extract(d.payload,'$.title')),json_extract(d.payload,'$.status')='failed',"+missing_index+",("+missing_description+") FROM documents d WHERE "+where+" AND (json_extract(d.payload,'$.status')='failed' OR "+missing_index+" OR ("+missing_description+")) ORDER BY d.rowid DESC LIMIT 20",args).fetchall()
            issues=[{'document_id':r[0],'source_name':r[1],'reasons':[reason for reason,flag in zip(['processing_failed','missing_text_index','missing_image_description'],r[2:]) if flag]} for r in issue_rows]
            generation=int(db.execute("SELECT value FROM meta WHERE key='generation'").fetchone()[0])
        images=counts[1] or 0
        return {'checked_at':now(),'knowledge_base_id':base,'corpus_generation':generation,'documents':counts[0],'images':images,'chunks':chunks[0],'active_chunks':chunks[1] or 0,'current_indexed_chunks':ready,'missing_chunk_indexes':max(0,(chunks[1] or 0)-ready),'described_images':described,'missing_image_descriptions':max(0,images-described),'cross_modal_indexed_images':indexed,'source_reviewed_image_labels':labels,'failed_documents':counts[3] or 0,'source_version_not_declared':counts[2] or 0,'ocr':ocr,'by_base':by_base,'structure_dispositions':structures,'current_evidence_reviews':reviews,'recent_source_reviews':recent,'task_states_global':task_states,'issues':issues,'counts_scope':'current active revisions with at least one retained model record; actual query models are recorded in Trace','whole_document_publication_is_separate':True,'source_version_supplementation_required':False}

    def diagnostics(self,check_services=False):
        with self.store.connection() as db:
            db.execute('BEGIN');integrity=db.execute('PRAGMA quick_check').fetchone()[0];tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            tasks=[{'task_id':r[0],'status':r[1],'kind':json.loads(r[2]).get('kind'),'error':(json.loads(r[2]).get('error') or '')[:1000]} for r in db.execute("SELECT task_id,status,payload FROM background_tasks WHERE status IN ('failed','partial','paused') ORDER BY rowid DESC LIMIT 12")] if 'background_tasks' in tables else []
            traces=[]
            for row in db.execute("SELECT trace_id,payload FROM traces WHERE json_extract(payload,'$.runtime.status')='failed' ORDER BY rowid DESC LIMIT 12"):
                v=json.loads(row[1]);traces.append({'trace_id':row[0],'question':v.get('original_query','')[:200],'stage':v.get('runtime',{}).get('stage'),'code':v.get('runtime',{}).get('code'),'error_type':v.get('runtime',{}).get('error_type')})
        environment={'python':platform.python_version(),'python_supported':sys.version_info>=(3,11),'platform':platform.system(),'sqlite':sqlite3.sqlite_version,'optional_modules':{n:bool(importlib.util.find_spec(n)) for n in ['fitz','docx','openpyxl','langgraph']}}
        probes=[]
        if check_services:
            http=build_opener(ProxyHandler({}))
            for name,key,default in [('chat','RAG_CHAT_URL',''),('retrieval','RAG_EMBEDDING_URL',''),('vision','RAG_VISION_URL','http://127.0.0.1:8083')]:
                raw=os.environ.get(key,default);u=urlparse(raw)
                if not raw:probes.append({'service':name,'status':'not_configured'});continue
                if u.scheme!='http' or u.hostname not in {'localhost','127.0.0.1','::1'} or u.username or u.password:probes.append({'service':name,'status':'external_not_probed'});continue
                try:
                    with http.open('http://'+u.netloc+'/health',timeout=2) as response:value=json.load(response)
                    probes.append({'service':name,'status':'ready','model':value.get('model') or value.get('vision_model') or value.get('embedding_model')})
                except Exception as exc:probes.append({'service':name,'status':'unavailable','error_type':type(exc).__name__})
        return {'checked_at':now(),'database':{'quick_check':integrity},'environment':environment,'services':probes,'services_probed':check_services,'failed_or_paused_tasks':tasks,'failed_traces':traces,'read_only':True,'full_restore_or_soak_test_performed':False}
