"""Read-only full audit of source bytes, OCR receipts, visual vectors and text replicas."""
import argparse,json,math,sqlite3,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.storage.knowledge_store import digest
from scripts.run_local_stack import local_environment
from app.runtime import RuntimeSession

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--store',default=str(ROOT/'data/runtime'))
    parser.add_argument('--baseline',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    import os
    os.environ.update(local_environment());runtime=RuntimeSession.from_env('configured');fingerprint=runtime.embedding.fingerprint
    root=Path(args.store);errors=[];counts=Counter();assets=set();hashes=set();dimensions=Counter();frame_counts=Counter()
    def check(condition,message):
        if not condition:errors.append(message)
    with sqlite3.connect(f'file:{root / "knowledge.sqlite3"}?mode=ro',uri=True) as db:
        db.execute('BEGIN');check(db.execute('pragma integrity_check').fetchone()[0]=='ok','SQLite integrity')
        docs=[json.loads(r[0]) for r in db.execute('select payload from documents where deleted=0')]
        chunks=[json.loads(r[0]) for r in db.execute('select payload from chunks')]
        by_id={c['chunk_id']:c for c in chunks};docmap={d['document_id']:d for d in docs}
        for d in docs:
            counts[d.get('knowledge_base_id','unknown')]+=1
            if not d.get('image_sha256'):continue
            hashes.add(d['source_sha256'])
            for path_field,sha_field in [('local_path','source_sha256'),('source_caption_asset','source_caption_sha256'),('source_metadata_asset','source_metadata_sha256')]:
                path=Path(d[path_field]);assets.add(str(path));check(path.resolve().parent==(root/'originals').resolve(),'Asset outside store '+d['document_id'])
                check(path.is_file() and digest(path.read_bytes())==d[sha_field],'Original mismatch '+d['document_id']+' '+path_field)
            row=db.execute('select payload from image_analysis_cache where cache_key=?',(d.get('image_analysis_key'),)).fetchone()
            check(row is not None,'Missing image analysis '+d['document_id'])
            if not row:continue
            a=json.loads(row[0]);vector=a['vector'];dimensions[len(vector)]+=1;frame_counts[a['frame_count']]+=1
            check(len(vector)==768 and all(math.isfinite(v) for v in vector) and abs(sum(v*v for v in vector)-1)<1e-5,'Invalid image vector '+d['document_id'])
            check(a['source_sha256']==d['source_sha256'] and a['fingerprint']==d['visual_embedding_model'],'Image fingerprint mismatch '+d['document_id'])
            check(d['ocr_status']==('completed' if a['text'].strip() else 'no_text'),'OCR state mismatch '+d['document_id'])
            text=a.get('first_frame_text',a['text'])
            expected=[text[n:n+1200] for n in range(0,len(text),1200)] if text.strip() else []
            for n,part in enumerate(expected,1):
                c=by_id.get(d['document_id']+'_ocr_'+str(n))
                check(c is not None and c['content']=='【图片 OCR 文字，自动识别未审核】\n'+part,'OCR chunk mismatch '+d['document_id'])
            if a.get('frame_count',1)>1:
                check(a.get('frames_processed')==a['frame_count'],'Animation has unprocessed frames '+d['document_id'])
                extra=a.get('additional_ocr_text','')
                for n in range(0,len(extra),1200):
                    c=by_id.get(d['document_id']+'_frame_ocr_'+str(n//1200+1))
                    check(c is not None and c['content']=='【图片 OCR 文字，自动识别未审核】\n'+extra[n:n+1200],'Frame OCR chunk mismatch '+d['document_id'])
                for frame in a.get('frame_analysis',[]):
                    check(digest(Path(frame['file']).read_bytes())==frame['sha256'],'Frame file changed '+d['document_id'])
            check(d['allow_cloud_upload'] is False and d['allow_training'] is False,'Material policy mismatch '+d['document_id'])
        vectors={r[0]:json.loads(r[1]) for r in db.execute('select cache_key,payload from vectors')}
        replicas={(r[0],r[1]) for r in db.execute('select chunk_id,revision_id from vector_records where fingerprint=?',(fingerprint,))}
        active=[c for c in chunks if c['metadata'].get('enabled',True) and c['metadata'].get('retrievable',True)]
        for c in active:
            key=digest((fingerprint+'\0'+c['content']).encode());vector=vectors.get(key)
            check(vector is not None,'Missing text embedding '+c['chunk_id'])
            if vector is not None:check(len(vector)==512 and all(math.isfinite(v) for v in vector) and abs(sum(v*v for v in vector)-1)<1e-4,'Invalid BGE vector '+c['chunk_id'])
            check((c['chunk_id'],c['metadata']['revision_id']) in replicas,'Missing current replica '+c['chunk_id'])
        with sqlite3.connect(f'file:{args.baseline}?mode=ro',uri=True) as baseline:
            original_chunks=[json.loads(r[0]) for r in baseline.execute('select payload from chunks')]
            for c in original_chunks:check(c['chunk_id'] in by_id and c['content']==by_id[c['chunk_id']]['content'],'Existing content changed '+c['chunk_id'])
            for row in baseline.execute('select payload from documents where deleted=0'):
                d=json.loads(row[0]);current=docmap.get(d['document_id'])
                check(current is not None,'Existing document removed '+d['document_id'])
                if current:
                    for field in ['source_sha256','source_key','knowledge_base_id','exclusive_to_system','allow_cloud_upload']:
                        check(current.get(field)==d.get(field),'Existing provenance changed '+d['document_id']+' '+field)
        report={'status':'passed' if not errors else 'failed','documents':len(docs),'images':sum(d.get('image_sha256') is not None for d in docs),
                'unique_image_sha256':len(hashes),'chunks':len(chunks),'active_text_vectors':len(active),'text_dimension':512,
                'visual_dimensions':dict(dimensions),'frame_counts':dict(frame_counts),'original_assets_checked':len(assets),
                'original_chunks_preserved':len(original_chunks),'by_knowledge_base':dict(counts),
                'ocr_with_text':sum(d.get('ocr_status')=='completed' for d in docs),'ocr_no_text':sum(d.get('ocr_status')=='no_text' for d in docs),
                'text_model':runtime.embedding.model_name,'text_fingerprint':fingerprint,'errors':errors,
                'review_scope':'machine_processing_integrity_not_industry_fact_accuracy','image_coverage':'all_decoded_frames'}
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False),flush=True)
    if errors:raise SystemExit(1)
if __name__=='__main__':main()
