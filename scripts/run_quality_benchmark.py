"""Run frozen knowledge-quality cases on a separate SQLite snapshot, never the live store."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',required=True,type=Path)
    parser.add_argument('--store',type=Path,default=ROOT/'data/runtime')
    parser.add_argument('--fixture-corpus',type=Path,help='Seed an isolated synthetic corpus instead of copying a database')
    parser.add_argument('--output-parent',type=Path,default=ROOT/'data/runtime/quality-benchmarks')
    parser.add_argument('--runtime-mode',choices=['offline','configured'],default='offline')
    parser.add_argument('--local-models',action='store_true',help='Use the existing verified localhost model profile')
    parser.add_argument('--table-retrieval',action='store_true')
    parser.add_argument('--evidence-retrieval',action='store_true')
    parser.add_argument('--cross-language-query',action='store_true')
    parser.add_argument('--modes',nargs='+',choices=['bm25','vector','hybrid'],default=['bm25','vector','hybrid'])
    parser.add_argument('--answer-ids',nargs='*',default=[])
    args=parser.parse_args()
    from app.evaluation.quality_benchmark import load_quality_cases,QualityBenchmark
    from app.storage.knowledge_store import digest
    cases=load_quality_cases(args.cases)
    source=(args.store/'knowledge.sqlite3').resolve()
    if not args.fixture_corpus and not source.is_file():raise FileNotFoundError('Source knowledge database is missing')
    output=args.output_parent.resolve()/uuid4().hex;output.mkdir(parents=True)
    database=output/'snapshot/knowledge.sqlite3';database.parent.mkdir()
    if args.local_models:
        from scripts.run_local_stack import local_environment
        os.environ.update(local_environment())
    from app.runtime import RuntimeSession
    from app.runtime.privacy import ensure_local_only
    runtime=RuntimeSession.from_env(args.runtime_mode);ensure_local_only(runtime,[],require_local=True)
    if not args.fixture_corpus:
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(database) as dst:
            src.backup(dst)
    from app.storage.knowledge_store import KnowledgeStore
    from app.core.engine import ConfigurableRagEngine
    engine=ConfigurableRagEngine(KnowledgeStore(database.parent))
    code_hashes={str(path.relative_to(ROOT)):digest(path.read_bytes()) for path in (
        ROOT/'app/core/engine.py',ROOT/'app/retrievers/table_retriever.py',ROOT/'app/retrievers/evidence_retriever.py',ROOT/'app/indexes/bm25_index.py',ROOT/'app/evaluation/quality_benchmark.py')}
    if args.fixture_corpus:
        from app.core.models import RagChunk
        from app.storage.review_store import ReviewStore
        fixture=json.loads(args.fixture_corpus.read_text())
        if fixture.get('data_source')!='synthetic_software_test' or any(c.review_status!='synthetic_contract' for c in cases):
            raise ValueError('Fixture mode accepts synthetic software tests only')
        for record in fixture['records']:
            engine.add_chunks([RagChunk(doc_id=record['doc_id'],chunk_id=record['chunk_id'],content=record['content'],
                metadata={**record['metadata'],'knowledge_base_id':record['knowledge_base_id']})])
            state=record.get('review_state')
            if state:
                reviews=ReviewStore(engine.store)
                for target in (['draft','published'] if state=='published' else [state]):
                    current=reviews.get(record['doc_id'])
                    reviews.transition(record['doc_id'],target,current['write_version'],current['review_version'],
                        'Synthetic fixture','Software test state, not human approval','Synthetic fixtures only; no production use')
    report=QualityBenchmark(engine).run(cases,args.runtime_mode,args.table_retrieval,args.modes,args.answer_ids,
        progress=lambda value:print(json.dumps(value,ensure_ascii=False),flush=True),evidence_retrieval=args.evidence_retrieval,cross_language_query=args.cross_language_query)
    report.update(eval_dataset=str(args.cases.resolve()),dataset_sha256=digest(args.cases.read_bytes()),
                  implementation_sha256=code_hashes,
                  source_database=str(source) if not args.fixture_corpus else None,isolated_database=str(database),
                  original_assets_shared_read_only=not bool(args.fixture_corpus),full_backup_restore_claimed=False)
    files=QualityBenchmark.write(report,output)
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(output),'status':report['status'],'metrics':report['metrics'],'reports':files},ensure_ascii=False),flush=True)
    if report['execution_errors']:raise SystemExit(1)


if __name__=='__main__':main()
