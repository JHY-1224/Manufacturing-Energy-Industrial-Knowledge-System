"""Local image OCR and visual vectors, with immutable analysis receipts and CAS revisions."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import os
from pathlib import Path
import platform
import subprocess
from threading import Lock
from uuid import uuid4

from app.core.models import RagChunk
from app.storage.knowledge_store import KnowledgeConflictError, digest, now
from app.storage.workbench_store import WorkbenchStore

_compile_lock = Lock()
SOURCE = Path(__file__).resolve().parents[2] / 'scripts/local_model/image_analysis.swift'


class LocalImageAnalyzer:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.fingerprint = 'apple-vision-v2:' + digest((digest(SOURCE.read_bytes()) + platform.mac_ver()[0]).encode())

    def recognize(self, path):
        if platform.system() != 'Darwin':
            raise ValueError('Local image analysis requires macOS 14 or later')
        directory = self.directory / 'tools'
        directory.mkdir(parents=True, exist_ok=True)
        binary = directory / ('image-analysis-' + digest(SOURCE.read_bytes())[:16])
        with _compile_lock:
            if not binary.exists():
                cache = directory / 'swift-module-cache'
                cache.mkdir(exist_ok=True)
                temporary = directory / ('image-build-' + uuid4().hex)
                result = subprocess.run(['/usr/bin/xcrun', 'swiftc', '-O', '-module-cache-path', str(cache), str(SOURCE), '-o', str(temporary)], capture_output=True, timeout=120)
                if result.returncode:
                    raise ValueError('Local image analysis compilation failed: ' + result.stderr.decode(errors='replace')[-1500:])
                try: os.link(temporary, binary)
                except FileExistsError: pass
        result = subprocess.run([str(binary), str(path)], capture_output=True, timeout=90)
        preprocessing=None
        if result.returncode and b'image-budget' in result.stderr:
            # A very large engineering diagram exceeds Vision's bounded input.
            # Preserve the source and produce a separate <= 36 MP processing copy.
            directory=self.directory/'derived';directory.mkdir(parents=True,exist_ok=True)
            derived=directory/(digest(Path(path).read_bytes())+'-vision-max6000.png')
            if not derived.exists():
                temporary=directory/('vision-resize-'+uuid4().hex+'.png')
                resize=subprocess.run(['/usr/bin/sips','-s','format','png','--resampleHeightWidthMax','6000',str(path),'--out',str(temporary)],capture_output=True,timeout=90)
                if resize.returncode:raise ValueError('Oversized image processing copy failed; original retained')
                try:os.link(temporary,derived)
                except FileExistsError:pass
            result=subprocess.run([str(binary),str(derived)],capture_output=True,timeout=90)
            preprocessing={'kind':'large_image_downscale','max_dimension':6000,'processing_copy':str(derived),
                           'processing_sha256':digest(derived.read_bytes()),'original_retained':True,
                           'limitation':'Very small diagram text may need source-resolution review'}
        if result.returncode:
            raise ValueError('Local image analysis failed: ' + result.stderr.decode(errors='replace')[-400:])
        payload = json.loads(result.stdout)
        if preprocessing:payload['preprocessing']=preprocessing
        vector = payload['vector']
        if len(vector) != payload['dimension'] or len(vector) < 64 or any(not math.isfinite(v) for v in vector):
            raise ValueError('Invalid visual vector')
        norm = math.sqrt(sum(v*v for v in vector))
        if norm <= 0: raise ValueError('Empty visual vector')
        payload['vector'] = [v/norm for v in vector]
        payload.update(fingerprint=self.fingerprint, provider='apple_vision', similarity_metric='cosine_normalized_features',
                       text='\n'.join(line['text'] for line in payload['lines']), visual_semantics='not_inferred',
                       technical_fact_review_status='unreviewed', completed_at=now())
        return payload


class ImageAnalysisService:
    def __init__(self, store, analyzer=None):
        self.store = store
        self.analyzer = analyzer or LocalImageAnalyzer(store.directory)
        with store.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS image_analysis_cache (cache_key TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL)')

    def key(self, source_sha256):
        return digest((self.analyzer.fingerprint + '\0' + source_sha256).encode())

    def frame_confidence_min(self, result):
        """Confidence over all decoded-frame OCR observations, never first-frame only."""
        values=[]
        with self.store.connection() as db:
            for frame in result.get('frame_analysis',[]):
                row=db.execute('SELECT payload FROM image_analysis_cache WHERE cache_key=?',(frame['analysis_key'],)).fetchone()
                if not row:return None
                values.extend(line['confidence'] for line in json.loads(row[0])['lines'])
        return min(values) if values else None

    def analyze(self, path, source_sha256):
        if digest(Path(path).read_bytes()) != source_sha256: raise ValueError('Original image checksum changed')
        key = self.key(source_sha256)
        with self.store.connection() as db:
            row = db.execute('SELECT payload FROM image_analysis_cache WHERE cache_key=?', (key,)).fetchone()
        if row:
            result = json.loads(row[0])
            if result['fingerprint'] != self.analyzer.fingerprint: raise ValueError('Image model changed')
            return key, result, True
        result = self.analyzer.recognize(path)
        result['source_sha256'] = source_sha256
        self.store.save_asset(self.store.encode(result).encode(), 'image-analysis.json')
        with self.store.connection() as db:
            db.execute('INSERT OR IGNORE INTO image_analysis_cache VALUES (?,?,?,?)', (key, source_sha256, self.analyzer.fingerprint, self.store.encode(result)))
        return key, result, False

    def process(self, document_id, expected_version, all_frames=False):
        document, chunks = WorkbenchStore(self.store).document_snapshot(document_id)
        key = self.key(document['source_sha256'])
        if all_frames:
            from app.knowledge.animation_analysis import VERSION
            key=digest((key+'\0'+VERSION).encode())
        if document.get('image_analysis_key') == key or (not all_frames and document.get('frame_count',1)>1 and document.get('frames_processed',1)==document['frame_count']):
            return {'already_processed': True, 'revision_id': document['revision_id'], 'cache_hit': True}
        if document['write_version'] != expected_version: raise KnowledgeConflictError('Image document changed')
        if not document.get('image_sha256'): raise ValueError('Document is not an image reference')
        if all_frames:
            from app.knowledge.animation_analysis import analyze_frames
            key,result,cache_hit=analyze_frames(self,document)
        else:key, result, cache_hit = self.analyze(document['local_path'], document['source_sha256'])
        revision = 'rev_' + uuid4().hex
        status = 'completed' if result['text'].strip() else 'no_text'
        common = {'image_analysis_key': key, 'image_analysis_status': 'ocr_and_visual_indexed', 'ocr_status': status,
                  'visual_embedding_model': result['fingerprint'], 'visual_embedding_dimension': result['dimension'],
                  'frame_count': result.get('frame_count', 1), 'frames_processed': result.get('frames_processed',1), 'visual_semantics': 'not_inferred'}
        if all_frames:common['frame_aggregation']=result['frame_aggregation']
        if result.get('preprocessing'):common['image_preprocessing']=result['preprocessing']
        document = deepcopy(document)
        document.update(common, revision_id=revision, updated_at=now(), processing_key=None)
        # Preserve every source/editor chunk. Never replace an edited OCR chunk.
        for chunk in chunks:
            chunk['metadata'].update(common, revision_id=revision)
            chunk['metadata'].pop('section_id', None)
        chunk_kind='image_frame_ocr' if all_frames else 'image_ocr'
        text=result.get('additional_ocr_text','') if all_frames else result['text']
        if text.strip() and not any(c['metadata'].get('chunk_kind') == chunk_kind for c in chunks):
            metadata = {k:v for k,v in chunks[0]['metadata'].items() if k not in {'section_id','parent_id','parent_content'}}
            confidence=self.frame_confidence_min(result) if all_frames else min((l['confidence'] for l in result['lines']),default=None)
            # Bound each indexed fragment; complete text and bounding boxes stay in receipt.
            for index in range(0, len(text), 1200):
                content = '【图片 OCR 文字，自动识别未审核】\n' + text[index:index+1200]
                chunks.append(RagChunk(doc_id=document_id, chunk_id=document_id+('_frame_ocr_' if all_frames else '_ocr_')+str(index//1200+1), content=content,
                    metadata={**metadata, 'chunk_kind':chunk_kind, 'evidence_kind':'ocr_text', 'parser':'apple_vision',
                              'parse_route':'ocr', 'section':'图片文字 '+str(index//1200+1), 'section_index':len(chunks)+1,
                              'start_offset':index, 'end_offset':min(index+1200,len(text)), 'ocr_confidence_min':confidence,
                              'ocr_confidence_scope':'all_frame_observations' if all_frames else 'single_image_observations',
                              'review_status':'machine_ocr_unreviewed'}).model_dump())
        document.update(chunk_count=len(chunks), parse_summary={'routes':['source_reference','ocr','visual_embedding'],
            'warnings':['OCR 未人工审核；视觉向量用于相似图检索，不代表工艺理解']})
        self.store.replace_document(document, chunks, {'operation':'image_analysis', 'image_analysis_key':key}, expected_version=expected_version)
        return {'revision_id':revision, 'ocr_characters':len(result['text']), 'ocr_status':status,
                'visual_dimension':result['dimension'], 'cache_hit':cache_hit, 'model_call':not cache_hit}

    def similar(self, document_id, base_id=None, top_k=10, review_mode='reference'):
        document=self.store.get_document(document_id)
        if not document: raise FileNotFoundError('Image document not found')
        from app.storage.review_store import ReviewStore
        state=ReviewStore(self.store).get(document_id)['state']
        if state=='withdrawn' or (review_mode=='published_only' and state!='published'):
            raise ValueError('Source image is outside the requested review scope')
        key=document.get('image_analysis_key')
        with self.store.connection() as db:
            source=db.execute('SELECT payload FROM image_analysis_cache WHERE cache_key=?',(key,)).fetchone()
            if not source: raise ValueError('Image analysis has not completed')
            query=json.loads(source[0]);rows=db.execute("SELECT d.payload,a.payload FROM documents d JOIN image_analysis_cache a ON a.cache_key=json_extract(d.payload,'$.image_analysis_key') LEFT JOIN knowledge_reviews r ON r.document_id=d.document_id WHERE d.deleted=0 AND a.fingerprint=? AND coalesce(r.state,'unreviewed')!='withdrawn' AND (?!='published_only' OR (r.state='published' AND r.revision_id=json_extract(d.payload,'$.revision_id'))) AND EXISTS (SELECT 1 FROM chunks c WHERE c.document_id=d.document_id AND coalesce(json_extract(c.payload,'$.metadata.enabled'),1)=1 AND coalesce(json_extract(c.payload,'$.metadata.retrievable'),1)=1)",(query['fingerprint'],review_mode)).fetchall()
        results=[]
        for d,a in rows:
            target=json.loads(d); analysis=json.loads(a)
            if base_id and target.get('knowledge_base_id')!=base_id: continue
            if target.get('exclusive_to_system') and target.get('knowledge_base_id')!=base_id: continue
            if target['document_id']==document_id: continue
            if len(query['vector'])!=len(analysis['vector']): continue
            score=sum(x*y for x,y in zip(query['vector'],analysis['vector']))
            results.append({'document_id':target['document_id'],'title':target.get('source_caption',target['title']),
                            'knowledge_base_id':target['knowledge_base_id'],'revision_id':target['revision_id'],
                            'score':max(-1,min(1,score)), 'source_page':target.get('source_page','')})
        return {'items':sorted(results,key=lambda r:r['score'],reverse=True)[:top_k], 'model':query['fingerprint'],
                'dimension':query['dimension'], 'metric':'cosine', 'scope':base_id,
                'interpretation':'视觉相似度，不是工艺正确率或同类标签概率'}
