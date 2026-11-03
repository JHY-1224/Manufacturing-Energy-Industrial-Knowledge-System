import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.knowledge.corpus_import import CosmeticsImporter
from app.knowledge.image_analysis import ImageAnalysisService
from app.storage.knowledge_store import KnowledgeStore, KnowledgeConflictError
from app.storage.workbench_store import WorkbenchStore
from app.core.models import RagChunk
from app.knowledge.industry_import import IndustryImporter
from app.storage.knowledge_store import digest


class Analyzer:
    fingerprint='synthetic-visual-fixture-v1'
    def __init__(self,text='电解槽 氢气出口'):self.calls=0;self.text=text;self.hook=None
    def recognize(self,path):
        self.calls+=1
        if self.hook:self.hook()
        return {'fingerprint':self.fingerprint,'vector':[1.0]+[0.0]*767,'dimension':768,'text':self.text,
                'lines':[{'text':self.text,'confidence':0.9,'bbox':[0,0,1,1]}], 'frame_count':1}


class ImagePipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=KnowledgeStore(self.temp.name);self.importer=CosmeticsImporter(self.store,Path(self.temp.name)/'run')
    def document(self,key='fixture',base='energy'):
        policy={'knowledge_base_id':base,'allow_cloud_upload':False}
        if base=='cosmetics_manufacturing':policy['exclusive_to_system']='cosmetics'
        result=self.importer._commit(key,key+'.png',b'fixture-image-'+key.encode(),
            {'doc_type':'image_source_reference','image_sha256':'fixture','source_caption':'原图说明'},
            [('original caption',{'chunk_kind':'source_caption','manually_edited':True})],policy=policy)
        return self.store.get_document(result['document_id'])
    def test_receipt_revision_and_idempotency_preserve_caption(self):
        d=self.document();a=Analyzer();service=ImageAnalysisService(self.store,a)
        r=service.process(d['document_id'],d['write_version'])
        self.assertEqual(r['visual_dimension'],768)
        current,chunks=WorkbenchStore(self.store).document_snapshot(d['document_id'])
        self.assertEqual(chunks[0]['content'],'original caption');self.assertTrue(chunks[0]['metadata']['manually_edited'])
        self.assertEqual(len(chunks),2);self.assertEqual(chunks[1]['metadata']['evidence_kind'],'ocr_text')
        self.assertNotEqual(current['revision_id'],d['revision_id'])
        service.process(d['document_id'],d['write_version']);self.assertEqual(a.calls,1)
        with self.store.connection() as db:self.assertEqual(db.execute('select count(*) from revisions').fetchone()[0],2)
    def test_no_text_keeps_source_and_builds_visual_index(self):
        d=self.document();r=ImageAnalysisService(self.store,Analyzer('')).process(d['document_id'],d['write_version'])
        self.assertEqual(r['ocr_status'],'no_text');self.assertEqual(self.store.get_document(d['document_id'])['chunk_count'],1)
    def test_stale_writer_and_modified_original_do_not_publish(self):
        d=self.document();service=ImageAnalysisService(self.store,Analyzer())
        with self.assertRaises(KnowledgeConflictError):service.process(d['document_id'],'stale')
        Path(d['local_path']).write_bytes(b'changed')
        with self.assertRaises(ValueError):service.process(d['document_id'],d['write_version'])
        self.assertNotIn('image_analysis_key',self.store.get_document(d['document_id']))
    def test_similarity_scope_and_exclusive_material(self):
        a=Analyzer();service=ImageAnalysisService(self.store,a)
        ds=[self.document('a','energy'),self.document('b','energy'),self.document('c','materials'),self.document('d','cosmetics_manufacturing')]
        for d in ds:service.process(d['document_id'],d['write_version'])
        r=service.similar(ds[0]['document_id'],'energy')
        self.assertEqual([i['document_id'] for i in r['items']],[ds[1]['document_id']])
        self.assertAlmostEqual(r['items'][0]['score'],1)
        self.assertEqual(len(service.similar(ds[0]['document_id'])['items']),2)

    def test_similarity_respects_published_only_and_withdrawal(self):
        from app.storage.review_store import ReviewStore
        service=ImageAnalysisService(self.store,Analyzer());reviews=ReviewStore(self.store)
        docs=[self.document(key,'energy') for key in ('review-a','review-b','review-c')]
        for doc in docs:service.process(doc['document_id'],doc['write_version'])
        def change(doc,state):
            current=reviews.get(doc['document_id'])
            return reviews.transition(doc['document_id'],state,current['write_version'],current['review_version'],'Fixture','Synthetic image review','Synthetic fixture only')
        for doc in docs[:2]:change(doc,'draft');change(doc,'published')
        self.assertEqual([r['document_id'] for r in service.similar(docs[0]['document_id'],'energy',review_mode='published_only')['items']],[docs[1]['document_id']])
        change(docs[1],'withdrawn')
        self.assertEqual(service.similar(docs[0]['document_id'],'energy',review_mode='published_only')['items'],[])
        change(docs[0],'withdrawn')
        with self.assertRaises(ValueError):service.similar(docs[0]['document_id'],'energy')

    def test_startup_replica_publication_skips_only_stale_revisions(self):
        d=self.document();_,old=WorkbenchStore(self.store).document_snapshot(d['document_id'])
        ImageAnalysisService(self.store,Analyzer()).process(d['document_id'],d['write_version'])
        records=WorkbenchStore(self.store);chunks=[RagChunk.model_validate(c) for c in old]
        with self.assertRaises(KnowledgeConflictError):records.record_vectors(chunks,'fixture','model',4,'test')
        records.record_vectors(chunks,'fixture','model',4,'test',strict=False)
        self.assertEqual(records.vector_records(chunks[0].chunk_id),[])

    def test_industry_import_does_not_inherit_cosmetics_identity(self):
        root=Path(self.temp.name)/'pack';root.mkdir();image=root/'image.png';image.write_bytes(b'fixture-picture')
        (root/'image.txt').write_text('PVC 工艺');(root/'image.json').write_text('{}')
        row={'id':'fixture','sha256':digest(image.read_bytes()),'file':'image.png','caption_file':'image.txt','metadata_file':'image.json',
             'industry_id':'materials','caption':'PVC 工艺','category':'PVC','category_id':'fixture','format':'PNG','width':300,'height':200,'saved_kind':'source_image'}
        item={'row':row,'root':str(root),'manifest_sha256':'fixed','caption_sha256':digest((root/'image.txt').read_bytes()),'metadata_sha256':digest((root/'image.json').read_bytes())}
        importer=IndustryImporter(self.store,Path(self.temp.name)/'run');result=importer.import_item(item)
        doc=self.store.get_document(result['document_id']);self.assertEqual(doc['knowledge_base_id'],'materials')
        self.assertFalse(doc['allow_cloud_upload']);self.assertNotIn('exclusive_to_system',doc)
        self.assertEqual(importer.import_item(item)['status'],'reused')
        (root/'image.txt').write_text('changed')
        with self.assertRaises(ValueError):importer.import_item(item)

    def test_all_frames_append_evidence_without_overwriting_first_frame(self):
        from app.knowledge.animation_analysis import VERSION
        from app.tasks.service import BackgroundTasks
        d=self.document();service=ImageAnalysisService(self.store,Analyzer())
        service.process(d['document_id'],d['write_version'])
        current,before=WorkbenchStore(self.store).document_snapshot(d['document_id'])
        key=digest((service.key(current['source_sha256'])+'\0'+VERSION).encode())
        result={'text':'电解槽 氢气出口\n【第 2/2 帧 OCR】\nSYN-FRAME-SECOND','additional_ocr_text':'【第 2/2 帧 OCR】\nSYN-FRAME-SECOND',
                'dimension':768,'fingerprint':service.analyzer.fingerprint,'frame_count':2,'frames_processed':2,'frame_aggregation':VERSION,
                'lines':[{'confidence':.9}]}
        with patch('app.knowledge.animation_analysis.analyze_frames',return_value=(key,result,False)):
            service.process(d['document_id'],current['write_version'],all_frames=True)
        final,after=WorkbenchStore(self.store).document_snapshot(d['document_id'])
        self.assertEqual(final['frames_processed'],2)
        self.assertEqual([c['content'] for c in after[:2]],[c['content'] for c in before])
        self.assertEqual(after[-1]['metadata']['chunk_kind'],'image_frame_ocr')
        self.assertEqual(after[-1]['metadata']['ocr_confidence_scope'],'all_frame_observations')
        self.assertIsNone(after[-1]['metadata']['ocr_confidence_min'])
        self.assertIn('SYN-FRAME-SECOND',after[-1]['content'])
        self.assertTrue(service.process(d['document_id'],current['write_version'])['already_processed'])

if __name__=='__main__':unittest.main()
