import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from app.core.models import RagChunk
from app.evaluation.quality_benchmark import QualityCase,load_quality_cases,validate_quality_sources,QualityBenchmark

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/knowledge_quality_v1'


class Snapshot:
    def __init__(self):
        self.chunks=[RagChunk(doc_id='doc',chunk_id='chunk',content='Group 4 &gt;800 MPa; condition A only.',
            metadata={'knowledge_base_id':'automotive','revision_id':'revision-1','source_sha256':'source-sha'})]
    def refresh(self):pass


class QualityBenchmarkTests(unittest.TestCase):
    def case(self,**updates):
        value={'query_id':'case-1','question':'Group 4 抗拉强度是多少MPa？','knowledge_base_id':'automotive',
            'category':'table','expected_doc_ids':['doc'],'expected_chunk_ids':['chunk'],'required_evidence':['>800','MPa']}
        value.update(updates);return QualityCase.model_validate(value)

    def test_reference_validation_preserves_case_sensitive_units_and_rejects_changed_revision(self):
        engine=Snapshot();validate_quality_sources(engine,[self.case()])
        with self.assertRaises(ValueError):validate_quality_sources(engine,[self.case(required_evidence=['mpa'])])
        with self.assertRaises(ValueError):validate_quality_sources(engine,[self.case(knowledge_base_id='energy')])
        with self.assertRaises(ValueError):validate_quality_sources(engine,[self.case(evidence_snapshots=[{'chunk_id':'chunk','revision_id':'old','content_sha256':'wrong'}])])

    def test_cases_cannot_claim_training_or_unattributed_human_review(self):
        for changes in ({'training_allowed':True},{'review_status':'human_reviewed'},
                        {'expected_empty':True},{'required_evidence':[]},{'required_evidence':[' ']}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.case(**changes)

    def test_training_reference_cannot_be_adopted_as_independent_gold(self):
        engine=Snapshot();engine.chunks[0].metadata['evidence_kind']='training_reference'
        with self.assertRaises(ValueError):validate_quality_sources(engine,[self.case()])

    def test_human_decisions_are_fingerprint_bound_and_never_overwrite_candidates(self):
        from scripts.apply_quality_review import apply
        from app.storage.knowledge_store import digest
        root=Path(tempfile.mkdtemp(prefix='quality-human-review-'))
        source=FIXTURE/'cases.jsonl';before=source.read_bytes();decision=root/'decisions.json'
        value={'dataset_sha256':digest(before),'reviewer':'Synthetic human-review fixture','reviewed_at':'2026-11-02T00:00:00Z',
               'decisions':[{'query_id':'quality_auto_01','decision':'approved','verified_source':True,'note':'Fixture source checked'}]}
        decision.write_text(json.dumps(value));output=root/'approved.jsonl'
        result=apply(source,decision,output)
        self.assertEqual(result['approved_cases'],1)
        self.assertEqual(load_quality_cases(output)[0].review_status,'human_reviewed')
        self.assertEqual(source.read_bytes(),before)
        with self.assertRaises(FileExistsError):apply(source,decision,output)
        value['dataset_sha256']='changed';decision.write_text(json.dumps(value))
        with self.assertRaises(ValueError):apply(source,decision,root/'invalid.jsonl')
        self.assertFalse((root/'invalid.jsonl').exists())

    def test_execution_errors_are_counted_as_failures_instead_of_dropped(self):
        class Broken(Snapshot):
            def query(self,request):raise RuntimeError('fixture failure')
        report=QualityBenchmark(Broken()).run([self.case()],modes=['bm25'])
        self.assertEqual(report['status'],'failed');self.assertEqual(report['execution_errors'],1)
        self.assertEqual(report['metrics']['document_hit_rate'],0)
        self.assertEqual(report['failed_cases'][0]['failure_type'],'execution_error')

    def test_recovered_evidence_ids_are_scored_as_actual_cited_evidence(self):
        from app.core.models import QueryTrace,RetrievalHit
        class Recovered(Snapshot):
            def query(self,request):
                return QueryTrace(original_query=request.question,rewritten_query=request.question,retrieval_mode='bm25',
                    reranked_docs=[RetrievalHit(doc_id='doc',chunk_id='ocr',content=self.chunks[0].content,score=1,
                        metadata={'knowledge_base_id':'automotive','evidence_chunk_ids':['chunk']})],
                    compressed_context=[self.chunks[0].content],runtime={'evidence_revision_checked':True})
        report=QualityBenchmark(Recovered()).run([self.case(expected_doc_ids=[])],modes=['bm25'])
        self.assertEqual(report['metrics']['chunk_hit_rate'],1)
        self.assertEqual(report['metrics']['document_hit_rate'],1)
        self.assertEqual(report['metrics']['required_evidence_present_rate'],1)

    def test_frozen_replay_requires_exact_options_and_never_generates_new_answers(self):
        from scripts.replay_quality_benchmark import TraceReplay
        from app.core.models import QueryTrace,QueryOptions,QueryRequest
        import sqlite3
        directory=Path(tempfile.mkdtemp(prefix='quality-replay-'));database=directory/'knowledge.sqlite3'
        options=QueryOptions(retrieval_mode='bm25')
        trace=QueryTrace(original_query='fixture query',rewritten_query='fixture query',retrieval_mode='bm25',options=options.model_dump(),runtime={'status':'completed'})
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE chunks(payload TEXT)');db.execute('CREATE TABLE traces(payload TEXT)')
            db.execute('INSERT INTO traces VALUES (?)',(trace.model_dump_json(),))
        engine=TraceReplay(database)
        self.assertEqual(engine.query(QueryRequest(question='fixture query',options=options,generate_answer=False)).trace_id,trace.trace_id)
        with self.assertRaises(ValueError):engine.query(QueryRequest(question='fixture query',options=options,generate_answer=True))
        with self.assertRaises(ValueError):engine.query(QueryRequest(question='fixture query',options=QueryOptions(retrieval_mode='vector'),generate_answer=False))

    def test_public_fixture_has_six_industries_independent_review_and_real_cross_scope_ids(self):
        cases=load_quality_cases(FIXTURE/'cases.jsonl')
        records=json.loads((FIXTURE/'corpus.json').read_text())['records']
        documents={r['doc_id']:r for r in records}
        self.assertEqual(len(cases),72)
        self.assertEqual(len({c.knowledge_base_id for c in cases}),6)
        self.assertEqual(sum(c.expected_empty for c in cases),12)
        for case in cases:
            self.assertEqual(case.review_status,'synthetic_contract')
            if case.category=='cross_scope':
                self.assertIn(case.document_id,documents)
                self.assertNotEqual(documents[case.document_id]['knowledge_base_id'],case.knowledge_base_id)

    def test_cli_all_72_cases_on_three_strategies_keeps_database_isolated(self):
        output=Path(tempfile.mkdtemp(prefix='quality-cli-test-'))
        command=[sys.executable,'-B',str(ROOT/'scripts/run_quality_benchmark.py'),
            '--cases',str(FIXTURE/'cases.jsonl'),'--fixture-corpus',str(FIXTURE/'corpus.json'),
            '--output-parent',str(output),'--table-retrieval']
        result=subprocess.run(command,cwd=ROOT,text=True,capture_output=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)
        last=json.loads(result.stdout.splitlines()[-1]);folder=Path(last['output'])
        self.assertTrue(folder.resolve().is_relative_to(output.resolve()))
        report=json.loads((folder/'result.json').read_text())
        self.assertEqual(report['sample_count'],216);self.assertEqual(report['failed_cases'],[])
        self.assertEqual(report['review_status_counts'],{'synthetic_contract':72})
        self.assertFalse(report['training_performed'])
        self.assertTrue(all(score==1 for score in report['metrics'].values()))


if __name__=='__main__':unittest.main()
