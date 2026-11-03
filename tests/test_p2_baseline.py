"""Protect the small, independent engineering baseline and its runnable isolation."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/p2_baseline'


class P2BaselineTests(unittest.TestCase):
    def test_gold_documents_belong_to_declared_scope(self):
        corpus=json.loads((FIXTURE/'corpus.json').read_text())['records']
        cases=[json.loads(line) for line in (FIXTURE/'queries.jsonl').read_text().splitlines()]
        documents={record['doc_id']:record for record in corpus}
        self.assertEqual(len(documents),8)
        self.assertEqual(len({record['chunk_id'] for record in corpus}),8)
        self.assertEqual(len({case['query_id'] for case in cases}),16)
        self.assertEqual(sum(case['expected_empty'] for case in cases),3)
        for case in cases:
            self.assertFalse(case['training_allowed'])
            self.assertEqual(case['review_status'],'synthetic_expected_facts')
            self.assertEqual(not case['gold_doc_ids'],case['expected_empty'])
            for identity in case['gold_doc_ids']:
                self.assertEqual(documents[identity]['knowledge_base_id'],case['knowledge_base_id'])
                self.assertIn('软件测试',documents[identity]['content'])
                self.assertTrue(case['required_answer_fragments'])

    def test_cli_runs_three_comparisons_in_an_isolated_store(self):
        directory=tempfile.mkdtemp(prefix='p2-baseline-test-')
        result=subprocess.run([sys.executable,str(ROOT/'scripts/run_p2_baseline.py'),'--output-parent',directory],cwd=ROOT,capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)
        result_path=Path(json.loads(result.stdout)['output'])/'result.json'
        self.assertTrue(result_path.is_relative_to(directory))
        report=json.loads(result_path.read_text())
        self.assertFalse(report['source_training_material_used'])
        self.assertFalse(report['official_ragas'])
        self.assertEqual(report['runtime_mode'],'offline')
        self.assertEqual(report['index_task']['counts'],{'completed':8})
        self.assertEqual({r['strategy'] for r in report['retrieval_experiments']},{'bm25','vector','hybrid'})
        for experiment in report['retrieval_experiments']:
            self.assertEqual(experiment['scope_checks_passed'],16)
            for sample in experiment['samples']:
                self.assertTrue(all(0<=value<=1 for value in sample['metrics'].values()))
                self.assertTrue(sample['trace_id'])
        self.assertEqual(report['answer_checks'],[])


if __name__=='__main__':unittest.main()
