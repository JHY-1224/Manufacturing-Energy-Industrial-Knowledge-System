"""Private local import of the cosmetics source collection; never executes source code."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from app.core.models import RagChunk
from app.storage.knowledge_store import KnowledgeConflictError, digest, now

BASE_ID='cosmetics_manufacturing'
SOURCE_SYSTEM='化妆品制造工艺AI系统'
COLLECTION='evaluation/local-only/google-factory-11categories-20261030-v1/图文数据集_1100张_最终版'
POLICY={'knowledge_base_id':BASE_ID,'source_system':SOURCE_SYSTEM,'exclusive_to_system':SOURCE_SYSTEM,
        'allow_cloud_upload':False,'allow_public_redistribution':False,'allow_training':False,
        'data_source':'private_corpus','domain':'化妆品制造工艺'}


def contained(root, relative):
    path=(root/relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Corpus reference escapes the collection or is missing')
    return path


def rows(path):
    result=[]
    for number,line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(),1):
        if not line.strip():continue
        value=json.loads(line)
        if not isinstance(value,dict):raise ValueError(f'Expected object at line {number}')
        result.append(value)
    return result


def text_training_files(source):
    candidates=[source/'化妆品资料采集包_20261025/cosmetics_sft_dataset_20261025_1650/data/train.messages.jsonl']
    candidates.extend(sorted((source/'data/text-training').rglob('train.messages.jsonl')))
    local=source/'local_training'
    if local.exists():
        for folder in sorted(local.iterdir()):
            if folder.is_dir() and folder.name.startswith('qwen-text-'):
                candidates.extend(sorted(folder.rglob('train.messages.jsonl')))
    return [p for p in candidates if p.is_file()]


def preflight(source):
    source=Path(source).resolve();collection=source/COLLECTION
    catalog=contained(collection,'总清单.jsonl')
    images=rows(catalog);ids=set();hashes=set();sizes=0
    for row in images:
        identity=row.get('id')
        if not isinstance(identity,str) or not identity or identity in ids:raise ValueError('Invalid or duplicate image ID')
        ids.add(identity)
        raw=contained(collection,row['file']).read_bytes()
        sha=digest(raw)
        if sha!=row.get('sha256') or len(raw)!=row.get('bytes'):raise ValueError('Original image checksum/size mismatch: '+row['file'])
        if sha in hashes:raise ValueError('Duplicate original image bytes in canonical collection')
        hashes.add(sha);sizes+=len(raw)
        if len(raw)>64*1024*1024:raise ValueError('Original image exceeds the asset budget')
        caption=contained(collection,row['caption_file']).read_text(encoding='utf-8-sig')
        sidecar=json.loads(contained(collection,row['metadata_file']).read_text(encoding='utf-8-sig'))
        if sidecar.get('sha256')!=sha:raise ValueError('Image sidecar checksum does not match catalog')
        if not caption.strip():raise ValueError('Empty source caption: '+row['caption_file'])
    files={}
    for path in text_training_files(source):
        if not path.resolve().is_relative_to(source):raise ValueError('Training source escapes source project')
        raw=path.read_bytes()
        if b'-----BEGIN PRIVATE KEY-----' in raw or b'-----BEGIN RSA PRIVATE KEY-----' in raw:
            raise ValueError('Unexpected credential material in training source')
        sha=digest(raw)
        if sha in files:
            files[sha]['aliases'].append(str(path.relative_to(source)));continue
        samples=rows(path)
        for row in samples:
            if row.get('split','train')!='train':raise ValueError('Non-training split encountered; evaluation material is not imported')
            messages=row.get('messages')
            if not isinstance(messages,list) or not messages:raise ValueError('Training record has no messages')
            if any(not isinstance(m,dict) or m.get('role') not in {'system','user','assistant'} or not isinstance(m.get('content'),str) for m in messages):
                raise ValueError('Only text training messages are accepted')
            if not any(m['role']=='user' for m in messages) or not any(m['role']=='assistant' for m in messages):raise ValueError('Training record lacks question/answer')
        files[sha]={'path':path,'sha256':sha,'aliases':[str(path.relative_to(source))],'samples':samples}
    return {'source':source,'collection':collection,'catalog_sha256':digest(catalog.read_bytes()),'images':images,
            'training':list(files.values()),'image_bytes':sizes,'training_records':sum(len(f['samples']) for f in files.values())}


class CosmeticsImporter:
    def __init__(self,store,run_directory):
        self.store=store;self.run=Path(run_directory);self.run.mkdir(parents=True,exist_ok=True)

    def _commit(self,key,filename,raw,metadata,contents,policy=None,id_prefix='doc_cosmetics_',provenance=None):
        policy=POLICY if policy is None else policy
        provenance=provenance or {'operation':'private_corpus_import','parser':'corpus_reference_import_v1',
            'route':'source_reference','warnings':['来源介绍或训练参考，不代表视觉识读或人工核验结论']}
        sha=digest(raw)
        existing=self.store.find_source(key,include_deleted=True)
        if existing:
            current=self.store.get_document(existing['document_id'])
            if not current:raise KnowledgeConflictError('Previously removed corpus document; not resurrected')
            if current.get('source_sha256')!=sha:raise KnowledgeConflictError('Source changed; import a separately versioned collection')
            for field in ('source_caption_sha256','source_metadata_sha256','source_manifest_sha256'):
                if field in metadata and current.get(field)!=metadata[field]:
                    raise KnowledgeConflictError('Source reference changed; import a separately versioned collection')
            if current.get('status')=='indexed':return {'status':'reused','document_id':current['document_id'],'chunks':current['chunk_count']}
        sha,asset=self.store.save_asset(raw,filename)
        document=self.store.register(key,{'document_id':id_prefix+digest(key.encode())[:24],
            'title':Path(filename).stem,'source_name':filename,'local_path':asset,'source_sha256':sha,
            'status':'uploaded','chunk_count':0,'size_bytes':len(raw),'doc_type':metadata['doc_type'],
            'created_at':now(),'updated_at':now(),**metadata,**policy})
        job=self.store.start_job(document['document_id'],{'operation':provenance['operation']},expected_version=document['write_version'])
        try:
            revision='rev_'+uuid4().hex
            chunks=[]
            for index,(text,attributes) in enumerate(contents,1):
                chunks.append(RagChunk(doc_id=document['document_id'],chunk_id=document['document_id']+'_item_'+str(index),content=text,
                    metadata={**metadata,**attributes,**policy,'source':filename,'document_id':document['document_id'],
                        'revision_id':revision,'section_index':index,'source_sha256':sha,'enabled':True,
                        'section':attributes.get('section',filename),'start_offset':None,'end_offset':None,
                        'parser':provenance['parser'],'parse_route':attributes.get('parse_route',provenance['route'])}).model_dump())
            document.update(revision_id=revision,status='indexed',chunk_count=len(chunks),updated_at=now(),
                processing_key=None,parse_summary={'routes':[provenance['route']],'warnings':provenance['warnings']})
            self.store.replace_document(document,chunks,{'operation':provenance['operation'],'knowledge_base_id':document.get('knowledge_base_id',BASE_ID)},job)
            return {'status':'imported','document_id':document['document_id'],'chunks':len(chunks)}
        except Exception:
            self.store.finish_job(job,'failed','Private corpus import failed; original and previous revision retained')
            raise

    def import_image(self,plan,row):
        collection=plan['collection'];image=contained(collection,row['file']);raw=image.read_bytes()
        if digest(raw)!=row['sha256']:raise ValueError('Image changed after preflight')
        caption_path=contained(collection,row['caption_file']);caption_raw=caption_path.read_bytes()
        sidecar=contained(collection,row['metadata_file']);sidecar_raw=sidecar.read_bytes()
        caption_sha,caption_asset=self.store.save_asset(caption_raw,caption_path.name)
        metadata_sha,metadata_asset=self.store.save_asset(sidecar_raw,sidecar.name)
        metadata={'doc_type':'image_source_reference','corpus_collection':'cosmetics-google-1100-final-20261030',
            'source_manifest_sha256':plan['catalog_sha256'],'original_relative_path':row['file'],
            'source_project_path':str(image),'source_page':row.get('source_page',''),'original_url':row.get('original_url',''),
            'source_record_id':row['id'],'source_caption_asset':caption_asset,'source_caption_sha256':caption_sha,
            'source_metadata_asset':metadata_asset,'source_metadata_sha256':metadata_sha,
            'assigned_category':row['assigned_category'],'category_role':'source_search_tag_not_verified_visual_label',
            'license_status':row.get('license_status','not_verified'),'review_status':'source_caption_unverified',
            'source_label_status':row.get('label_status','unverified'),'saved_kind':row.get('saved_kind'),
            'image_format':row['format'],'image_width':row['width'],'image_height':row['height'],
            'image_sha256':row['sha256'],'image_analysis_status':'not_run','evidence_kind':'source_caption',
            'figure_id':'figure_'+row['sha256'][:24]}
        text='【来源网页介绍，未经核验；不是视觉识读或缺陷标注】\n'+caption_raw.decode('utf-8-sig').strip()+'\n来源搜索分类：'+row['assigned_category']
        return self._commit('corpus:cosmetics:google1100:'+row['id'],image.name,raw,metadata,[(text,{'chunk_kind':'source_caption','section':row['assigned_category'],'category':'Figure'})])

    def import_training(self,plan,item):
        path=item['path'];raw=path.read_bytes()
        if digest(raw)!=item['sha256']:raise ValueError('Training source changed after preflight')
        version=path.parent.name if path.parent.name!='data' else 'cosmetics-sft-20261025'
        metadata={'doc_type':'text_training_reference','dataset_version':version,'original_relative_path':str(path.relative_to(plan['source'])),
                  'source_project_path':str(path),'source_aliases':item['aliases'],'dataset_split':'train',
                  'review_status':'training_reference_not_independently_verified','evidence_kind':'training_reference',
                  'system_messages_indexed':False,'license_status':'source_terms_preserved_not_publicly_released'}
        content=[]
        for index,row in enumerate(item['samples'],1):
            # Policy instructions remain in the immutable raw file, not in evidence.
            parts=[('问题：' if m['role']=='user' else '训练参考回答：')+m['content'] for m in row['messages'] if m['role']!='system']
            identity=str(row.get('id') or row.get('sample_id') or index)
            content.append(('【文本训练参考；未作为已核验工艺事实】\n'+'\n\n'.join(parts),
                            {'category':'Case','chunk_kind':'training_reference','case_id':identity,'source_line':index,
                             'section':version+' · '+identity,'original_sample_id':row.get('original_sample_id'),'source_view':row.get('view')}))
        return self._commit('corpus:cosmetics:training:'+item['sha256'],version+'-train.messages.jsonl',raw,metadata,content)

    def execute(self,plan,limit=None):
        results=[];failures=[]
        tasks=[('image',row) for row in plan['images']]+[('training',item) for item in plan['training']]
        for number,(kind,item) in enumerate(tasks,1):
            if limit is not None and number>limit:break
            identity=item['id'] if kind=='image' else item['sha256']
            try:result={'kind':kind,'source_id':identity,**(self.import_image(plan,item) if kind=='image' else self.import_training(plan,item))}
            except Exception as exc:
                result={'kind':kind,'source_id':identity,'status':'failed','error_type':type(exc).__name__,'message':str(exc)};failures.append(result)
            with (self.run/'progress.jsonl').open('a',encoding='utf-8') as handle:handle.write(json.dumps(result,ensure_ascii=False)+'\n')
            results.append(result)
            if number%100==0:print(f'Processed {number}/{len(tasks)}; failures={len(failures)}',flush=True)
        output={'status':'failed' if failures else 'completed' if len(results)==len(tasks) else 'partial',
                'knowledge_base_id':BASE_ID,'source_system':SOURCE_SYSTEM,'planned_images':len(plan['images']),
                'image_bytes':plan['image_bytes'],'planned_training_files':len(plan['training']),'source_training_records':plan['training_records'],
                'processed_documents':len(results),'imported':sum(r['status']=='imported' for r in results),
                'reused':sum(r['status']=='reused' for r in results),'failures':failures,
                'image_semantic_analysis':'not_run','source_caption_review':'unverified','model_training_performed':False,
                'all_materials_local_only':True,'completed_at':now()}
        target=self.run/('result-'+uuid4().hex+'.json');target.write_text(json.dumps(output,ensure_ascii=False,indent=2))
        print('Result: '+str(target),flush=True)
        return output
