"""Current image/OCR geometry and append-only user corrections of visible labels."""
import json,math
from app.knowledge.multimodal import MultimodalService
from app.knowledge.visual_screening import screen
from app.storage.evidence_review_store import EvidenceReviewStore
from app.storage.knowledge_store import KnowledgeConflictError

class EvidenceWorkbench:
    def __init__(self,store):self.store=store;self.reviews=EvidenceReviewStore(store)

    def view(self,document_id,region_page=1,region_page_size=100):
        if region_page<1 or not 1<=region_page_size<=200:raise ValueError('Invalid OCR region page')
        document=self.store.get_document(document_id)
        if not document:raise FileNotFoundError('Document not found')
        if not document.get('image_sha256'):raise ValueError('Select an image document; PDF table regions are available in Structure Evidence')
        with self.store.connection() as db:
            tables={v[0] for v in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            row=db.execute('SELECT payload,source_sha256 FROM image_analysis_cache WHERE cache_key=?',(document.get('image_analysis_key'),)).fetchone() if 'image_analysis_cache' in tables else None
        if row and row[1]!=document.get('source_sha256'):raise KnowledgeConflictError('OCR regions belong to a different source')
        analysis=json.loads(row[0]) if row else {};regions=[];invalid=0
        for i,line in enumerate(analysis.get('lines',[])):
            box=line.get('bbox',[])
            if len(box)!=4 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in box) or not all(0<=v<=1 for v in box) or box[0]+box[2]>1.001 or box[1]+box[3]>1.001:invalid+=1;continue
            confidence=line.get('confidence');confidence=confidence if isinstance(confidence,(int,float)) and math.isfinite(confidence) and 0<=confidence<=1 else None
            regions.append({'region_id':'ocr_'+str(i),'text':str(line.get('text','')),'confidence':confidence,'bbox':box,'coordinate_system':'normalized_top_left_xywh','source':analysis.get('provider','unknown')+'_ocr','frame_index':0})
        observations=MultimodalService(self.store).observations(document_id)['items']
        current=observations[0] if observations else None;reviews=self.reviews.current(document_id)['items']
        latest=next((v for v in reviews if v['scope']=='visual_source_labels' and v['evidence'].get('source_sha256')==document.get('source_sha256') and current and v['evidence'].get('fingerprint')==current['fingerprint']),None)
        start=(region_page-1)*region_page_size
        return {'document_id':document_id,'revision_id':document.get('revision_id'),'write_version':document['write_version'],'source_sha256':document.get('source_sha256'),'source_name':document.get('source_name',document.get('title')),'knowledge_base_id':document.get('knowledge_base_id'),'width':document.get('image_width') or analysis.get('width'),'height':document.get('image_height') or analysis.get('height'),'regions':regions[start:start+region_page_size],'region_total':len(regions),'region_page':region_page,'region_page_size':region_page_size,'invalid_regions_omitted':invalid,'ocr_status':document.get('ocr_status','not_processed'),'ocr_model':analysis.get('fingerprint'),'ocr_provider':analysis.get('provider'),'region_scope':'original first frame only; model observations have no invented region coordinates','observations':observations,'latest_label_review':latest,'source_reviews':reviews,'whole_document_published':False}

    def review(self,document_id,*,expected_revision,expected_write_version,expected_source_sha256,expected_model_fingerprint,expected_label_review_id,source_visible_description,reviewer,note,decision,source_inspected):
        if not reviewer.strip() or not note.strip():raise ValueError('Reviewer and a source-check note are required')
        if not source_inspected:raise ValueError('Confirm viewing the original before saving source-visible labels')
        view=self.view(document_id)
        if (view['revision_id'],view['write_version'],view['source_sha256'])!=(expected_revision,expected_write_version,expected_source_sha256):raise KnowledgeConflictError('Image source changed; refresh the comparison')
        if not view['observations'] or view['observations'][0]['fingerprint']!=expected_model_fingerprint:raise KnowledgeConflictError('Observation batch changed; refresh the comparison')
        value={'document_id':document_id,'revision_id':expected_revision,'source_sha256':expected_source_sha256,'observation':{'visible_description':source_visible_description}}
        if decision not in {'accepted','corrected','rejected'}:raise ValueError('Unknown source-label decision')
        if decision!='rejected' and not screen(value,{'revision_id':expected_revision,'source_sha256':expected_source_sha256})['ai_screening']['retrieval_allowed']:raise ValueError('Use visible objects and layout; keep unverified numbers, causes and operating instructions out of retrieval labels')
        result=self.reviews.append(document_id,expected_revision,decision,'visual_source_labels',note.strip(),{'fingerprint':expected_model_fingerprint,'source_sha256':expected_source_sha256,'source_inspected':True,'source_visible_description':source_visible_description.strip(),'inspection':'workbench original/OCR region comparison','scope':'visible_first_frame_objects_and_layout_only','technical_fact_approved':False},reviewer.strip(),expected_write_version=expected_write_version,expected_latest_review_id=expected_label_review_id,review_kind='user_source_review')
        return {'review':result,'current_revision_unchanged':True,'effective_in_description_retrieval':decision!='rejected','whole_document_published':False,'human_gold_claimed':False}
