import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from app.core.models import RagChunk,QueryOptions,QueryRequest
from app.core.engine import ConfigurableRagEngine
from app.retrievers.evidence_retriever import EvidenceRetriever
from app.storage.knowledge_store import KnowledgeStore
from app.storage.workbench_store import WorkbenchStore

def c(i,text,base='electronics'):
 return RagChunk(doc_id='d'+i,chunk_id=i,content=text,metadata={'knowledge_base_id':base})

class Round5RetrievalTests(unittest.TestCase):
 def test_definition_recall_uses_source_terms_not_embedded_answers(self):
  good=c('good','Acronyms\nXYZ synthetic xenon zone. 1, 4\nRST random sample test. 2')
  noise=[c(str(i),'XYZ and RST are mentioned in this robot report. '*20) for i in range(15)]
  hits=EvidenceRetriever().search('报告缩写表 XYZ 与 RST 全称是什么',[*noise,good],6)
  self.assertEqual(hits[0].chunk_id,'good');self.assertEqual(hits[0].metadata['evidence_definition_terms'],['XYZ','RST']);self.assertEqual(hits[0].content,good.content)
 def test_window_cache_reuse_and_same_id_content_change(self):
  retriever=EvidenceRetriever();first=c('a','unique old source');retriever.search('old',[first]);self.assertFalse(retriever.last_cache_stats['reused'])
  retriever.search('old',[first]);self.assertTrue(retriever.last_cache_stats['reused'])
  newer=c('a','replacement evidence');hits=retriever.search('replacement',[newer]);self.assertFalse(retriever.last_cache_stats['reused']);self.assertEqual(hits[0].content,newer.content)
 def test_cache_does_not_leak_removed_or_foreign_scope(self):
  retriever=EvidenceRetriever();retriever.search('SECRET',[c('private','SECRET','cosmetics_manufacturing')]);self.assertEqual(retriever.search('SECRET',[c('public','ordinary')]),[])
 def test_unicode_minus_not_claimed_as_positive_number(self):
  hits=EvidenceRetriever().search('temperature 73 °C',[c('a','temperature −73 °C')]);self.assertEqual(hits[0].metadata['evidence_numeric_matches'],[])
 def test_engine_cache_scoped_and_invalidated_by_revision(self):
  store=KnowledgeStore(Path(tempfile.mkdtemp()));engine=ConfigurableRagEngine(store);engine.add_chunks([c('a','sample evidence')]);q=QueryRequest(question='sample',options=QueryOptions(retrieval_mode='bm25',evidence_retrieval=True),generate_answer=False)
  first=engine.query(q);second=engine.query(q);self.assertFalse(first.runtime['evidence_retrieval']['window_index']['reused']);self.assertTrue(second.runtime['evidence_retrieval']['window_index']['reused'])
  engine.add_chunks([c('a','new replacement')]);third=engine.query(q);self.assertFalse(third.runtime['evidence_retrieval']['window_index']['reused'])
 def test_session_pagination_stable_and_archived_hidden(self):
  records=WorkbenchStore(KnowledgeStore(Path(tempfile.mkdtemp())))
  sessions=[records.create_session('electronics',str(i)) for i in range(65)];records.archive_session(sessions[0]['session_id'])
  pages=[records.sessions_page(i,25) for i in (1,2,3)]
  self.assertEqual([len(p['items']) for p in pages],[25,25,14]);self.assertTrue(all(p['total']==64 for p in pages));self.assertFalse(pages[-1]['has_more'])
  self.assertEqual(len({x['session_id'] for p in pages for x in p['items']}),64);self.assertEqual(records.sessions_page(1,20,'energy')['total'],0)
  with self.assertRaises(ValueError):records.sessions_page(0,50)


class VersionEvidenceTests(unittest.TestCase):
 def test_source_edition_line_outweighs_prose_years(self):
  from app.core.models import RagChunk
  from app.retrievers.evidence_retriever import EvidenceRetriever
  good=RagChunk(doc_id='synthetic',chunk_id='cover',content='Seventh Edition, 2017\nPrepared for Synthetic Publisher',metadata={})
  other=RagChunk(doc_id='synthetic',chunk_id='body',content='Synthetic process heating in 1999 and 2016, bibliography references.',metadata={})
  retriever=EvidenceRetriever();hits=retriever.search('合成手册扉页写明第几版？',[good,other]);self.assertEqual(hits[0].chunk_id,'cover');self.assertEqual(hits[0].metadata['evidence_version_lines'],['Seventh Edition, 2017'])
  plain=retriever.search('Synthetic process heating',[good,other]);self.assertTrue(all(not h.metadata['evidence_version_lines'] for h in plain))
