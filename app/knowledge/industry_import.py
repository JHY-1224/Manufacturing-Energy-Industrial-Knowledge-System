"""Import the finalized industry collections without collecting archived candidates."""
import json
from pathlib import Path
from collections import Counter
from app.knowledge.catalog import validate_base, KNOWLEDGE_BASES
from app.knowledge.corpus_import import CosmeticsImporter, contained
from app.storage.knowledge_store import digest


def preflight(runtime):
    runtime=Path(runtime).resolve()
    broad=runtime/'industry-images-20261102-v1'
    process=runtime/'process-manufacturing-images-20261102-v1/已筛选流程制造图文-v1'
    packs=[(broad,broad/'总清单.jsonl',{'automotive','high_end_equipment','electronics'}),
           (broad,broad/'精选补充/总清单.jsonl',{'automotive','high_end_equipment','electronics'}),
           (process,process/'总清单.jsonl',{'materials','energy'})]
    records=[];seen=set()
    for root,manifest,allowed in packs:
        manifest_sha=digest(manifest.read_bytes())
        for line in manifest.read_text().splitlines():
            if not line.strip():continue
            row=json.loads(line)
            if row['industry_id'] not in allowed:continue
            validate_base(row['industry_id'])
            if row['sha256'] in seen:raise ValueError('Duplicate image in finalized collections')
            seen.add(row['sha256'])
            paths={key:contained(root,row[key]) for key in ('file','caption_file','metadata_file')}
            if digest(paths['file'].read_bytes())!=row['sha256']:raise ValueError('Image checksum mismatch')
            caption=paths['caption_file'].read_text(encoding='utf-8-sig').strip()
            if not caption:raise ValueError('Missing source caption')
            # Validate sidecar JSON while keeping it out of evidence/prompt instructions.
            json.loads(paths['metadata_file'].read_text())
            records.append({'row':{k:v for k,v in row.items() if k not in {'thumbnail','thumbnail_url','preview'}},
                'root':str(root),'manifest_sha256':manifest_sha,'caption_sha256':digest(paths['caption_file'].read_bytes()),
                'metadata_sha256':digest(paths['metadata_file'].read_bytes())})
    return {'items':records,'counts':dict(Counter(r['row']['industry_id'] for r in records))}


class IndustryImporter(CosmeticsImporter):
    def import_item(self, item):
        row=item['row'];root=Path(item['root']);image=contained(root,row['file'])
        raw=image.read_bytes()
        if digest(raw)!=row['sha256']:raise ValueError('Image changed since preflight')
        assets={}
        for field in ('caption','metadata'):
            path=contained(root,row[field+'_file']);content=path.read_bytes()
            if digest(content)!=item[field+'_sha256']:raise ValueError('Reference changed since preflight')
            sha,asset=self.store.save_asset(content,path.name)
            assets['source_'+field+'_sha256']=sha;assets['source_'+field+'_asset']=asset
        base=row['industry_id'];name=next(b['name'] for b in KNOWLEDGE_BASES if b['id']==base)
        metadata={**assets,'knowledge_base_id':base,'domain':name,'doc_type':'image_source_reference',
            'corpus_collection':'industrial-finalized-20261102-v1','source_manifest_sha256':item['manifest_sha256'],
            'source_project_path':str(image),'original_relative_path':row['file'],'source_record_id':row['id'],
            'source_page':row.get('source_page',''),'original_url':row.get('original_url',''),
            'source_caption':row['caption'],'assigned_category':row['category'],'source_search_category_id':row['category_id'],
            'category_role':'source_search_tag_not_verified_visual_label','review_status':'source_caption_unverified',
            'image_sha256':row['sha256'],'figure_id':'figure_'+row['sha256'][:24],
            'image_format':row['format'],'image_width':row['width'],'image_height':row['height'],
            'saved_kind':row['saved_kind'],'image_analysis_status':'not_run','evidence_kind':'source_caption',
            'license_status':row.get('license_status','not_verified'),'technical_fact_review_status':'unreviewed',
            'collection_track':row.get('collection_track','search_reference'),
            'visual_relevance_review':row.get('selection_status','pending'),
            'visible_topic_or_use':row.get('visible_topic_or_use',row.get('selection_reason',''))}
        text='【来源网页介绍，未经核验】\n'+row['caption']+'\n行业：'+name+'\n来源搜索分类：'+row['category']
        policy={'allow_cloud_upload':False,'allow_public_redistribution':False,'allow_training':False,'data_source':'local_industry_collection'}
        return self._commit('corpus:industrial:20261102:'+row['sha256'],image.name,raw,metadata,
            [(text,{'chunk_kind':'source_caption','section':row['category'],'category':'Figure'})],policy=policy,id_prefix='doc_industry_')
