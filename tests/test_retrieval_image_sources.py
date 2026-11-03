import tempfile
import unittest
from app.core.engine import ConfigurableRagEngine
from app.core.models import RagChunk, QueryOptions, QueryRequest, RetrievalHit
from app.indexes.bm25_index import BM25Index
from app.storage.knowledge_store import KnowledgeStore, KnowledgeConflictError
from app.storage.workbench_store import WorkbenchStore


class RetrievalImageSourceTests(unittest.TestCase):
    def test_image_caption_and_ocr_keep_punctuation_whitespace_and_order_after_compression(self):
        caption='【来源网页介绍，未经核验】\n合成图片为何出现问题？ - SYN-SOURCE\n一次性说明！,原文后缀'
        ocr='【图片 OCR 文字，自动识别未审核】\n步骤一。步骤二。'
        for include_ocr in [False,True]:
            rows=[RagChunk(doc_id='syn-image',chunk_id='syn-caption',content=caption,
                metadata={'knowledge_base_id':'materials','chunk_kind':'source_caption','image_sha256':'synthetic'})]
            if include_ocr:rows.append(RagChunk(doc_id='syn-image',chunk_id='syn-ocr',content=ocr,
                metadata={'knowledge_base_id':'materials','chunk_kind':'image_ocr','image_sha256':'synthetic'}))
            self.engine.add_chunks(rows)
            trace=self.engine.query(QueryRequest(question='合成图片为何出现问题？ - SYN-SOURCE',generate_answer=False,
                options=QueryOptions(retrieval_mode='bm25',rerank=False,query_expansion=False,query_rewrite=False,
                    metadata_router=False,query_transformation=False,metadata_filter={'knowledge_base_id':'materials'})))
            context=next(c for h,c in zip(trace.reranked_docs,trace.compressed_context) if h.doc_id=='syn-image')
            self.assertIn(caption,context)
            if include_ocr:self.assertIn(ocr,context);self.assertLess(context.index(caption),context.index(ocr))

    def test_recovered_image_blocks_are_complete_and_omissions_are_explicit(self):
        from app.post_retrieval.image_context import ImageContextRecover
        rows=[RagChunk(doc_id='d',chunk_id='cap',content='完整介绍？ - 来源',metadata={'chunk_kind':'source_caption'}),
            RagChunk(doc_id='d',chunk_id='long',content='OCR '*100,metadata={'chunk_kind':'image_ocr'})]
        hit=RetrievalHit(doc_id='d',chunk_id='cap',content=rows[0].content,score=1,metadata={'image_sha256':'fixture'})
        value=ImageContextRecover().recover('OCR',[hit],rows,max_characters=80)[0]
        self.assertIn(rows[0].content,value.content);self.assertNotIn('OCR',value.content)
        self.assertEqual(value.metadata['evidence_chunk_ids'],['cap'])
        self.assertEqual(value.metadata['image_context_omitted_chunk_ids'],['long'])
        self.assertLessEqual(len(value.content),80)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=KnowledgeStore(self.temp.name);self.engine=ConfigurableRagEngine(self.store)
        self.engine.add_chunks([RagChunk(doc_id='source',chunk_id='source_caption',content='SYN-PVC extrusion process flow. '+('synthetic test context. '*30),metadata={'knowledge_base_id':'materials'})])

    def test_summary_cites_real_chunk_and_source_revision(self):
        trace=self.engine.query(QueryRequest(question='SYN-PVC',generate_answer=False,options=QueryOptions(retrieval_mode='summary',metadata_filter={'knowledge_base_id':'materials'},query_rewrite=False,query_expansion=False,query_transformation=False,metadata_router=False)))
        self.assertTrue(trace.reranked_docs)
        self.assertEqual(trace.reranked_docs[0].chunk_id,'source_caption')
        self.assertEqual(trace.reranked_docs[0].metadata['source_chunk_id'],'source_caption')
        self.assertTrue(trace.runtime['evidence_revision_checked'])

    def test_unrelated_ingestion_allowed_but_changed_evidence_rejected(self):
        chunk=self.store.snapshot()[1][0]
        hit=RetrievalHit(doc_id=chunk['doc_id'],chunk_id=chunk['chunk_id'],content=chunk['content'],metadata=chunk['metadata'],score=1)
        records=WorkbenchStore(self.store)
        self.engine.add_chunks([RagChunk(doc_id='unrelated',chunk_id='other',content='unrelated fixture',metadata={'knowledge_base_id':'energy'})])
        self.assertIsInstance(records.assert_sources_current([hit]),int)
        self.engine.add_chunks([RagChunk(doc_id='source',chunk_id='source_caption',content='new version',metadata={'knowledge_base_id':'materials'})])
        with self.assertRaises(KnowledgeConflictError):records.assert_sources_current([hit])

    def test_bm25_does_not_index_paths_hashes_and_policy_fields(self):
        index=BM25Index();index.build([RagChunk(doc_id='d',chunk_id='c',content='PVC 工艺',metadata={'source_caption':'聚合 extrusion','source_sha256':'internal-provenance-only','local_path':'/internal-only/path'})])
        self.assertTrue(index.search('extrusion',5));self.assertEqual(index.search('internal-provenance-only',5),[])
        self.assertEqual(index.search('/internal-only/path',5),[])

    def test_caption_recovers_same_document_ocr_and_resolvable_source_ids(self):
        from app.post_retrieval.image_context import ImageContextRecover
        rows=[RagChunk(doc_id='image',chunk_id='caption',content='工艺图',metadata={'chunk_kind':'source_caption'}),
              RagChunk(doc_id='image',chunk_id='ocr',content='SYN-MIX-01 合成搅拌步骤',metadata={'chunk_kind':'image_ocr'}),
              RagChunk(doc_id='other',chunk_id='other_ocr',content='PRIVATE OTHER DOCUMENT',metadata={'chunk_kind':'image_ocr'})]
        hit=RetrievalHit(doc_id='image',chunk_id='caption',content='工艺图',score=1,metadata={'image_sha256':'fixture'})
        result=ImageContextRecover().recover('搅拌',[hit],rows)[0]
        self.assertIn('SYN-MIX-01',result.content);self.assertNotIn('PRIVATE',result.content)
        self.assertEqual(result.metadata['evidence_chunk_ids'],['caption','ocr'])
        from app.post_retrieval.deduplicator import Deduplicator
        other=result.model_copy(deep=True,update={'chunk_id':'ocr'})
        self.assertEqual(len(Deduplicator().deduplicate([result,other])),1)

    def test_recovered_evidence_is_validated_in_addition_to_primary_chunk(self):
        chunk=self.store.snapshot()[1][0]
        hit=RetrievalHit(doc_id=chunk['doc_id'],chunk_id=chunk['chunk_id'],content=chunk['content'],
            metadata={**chunk['metadata'],'evidence_chunk_ids':['missing_ocr']},score=1)
        with self.assertRaises(KnowledgeConflictError):WorkbenchStore(self.store).assert_sources_current([hit])

    def test_title_pipe_is_not_a_markdown_table(self):
        from app.post_retrieval.context_compressor import ContextCompressor
        text='Source title | Website。无关句。SYN-TARGET 这是需要保留的末尾证据。'
        value=ContextCompressor().compress('SYN-TARGET',text,max_sentences=1)
        self.assertIn('SYN-TARGET',value);self.assertNotIn('Website',value)
