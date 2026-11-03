"""Rescore frozen traces after source-based gold corrections, with zero new model calls."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.core.models import RagChunk,QueryTrace
from app.evaluation.quality_benchmark import load_quality_cases,QualityBenchmark
from app.storage.knowledge_store import digest


class TraceReplay:
    def __init__(self, database):
        with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True) as db:
            self.chunks=[RagChunk.model_validate(json.loads(r[0])) for r in db.execute('SELECT payload FROM chunks')]
            self.traces={}
            for row in db.execute('SELECT payload FROM traces'):
                trace=QueryTrace.model_validate(json.loads(row[0]))
                key=(trace.original_query,json.dumps(trace.options,sort_keys=True))
                self.traces[key]=trace
    def refresh(self):pass
    def query(self,request):
        key=(request.question,json.dumps(request.options.model_dump(),sort_keys=True))
        trace=self.traces.get(key)
        if not trace or trace.runtime.get('status')!='completed':raise ValueError('No completed trace for these exact query options')
        if request.generate_answer and not trace.answer:raise ValueError('Replay cannot generate an answer absent from the original trace')
        return trace.model_copy(deep=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-report',required=True,type=Path);parser.add_argument('--cases',required=True,type=Path)
    parser.add_argument('--output-parent',required=True,type=Path);args=parser.parse_args()
    source=args.source_report.resolve();original=json.loads(source.read_text());database=Path(original['isolated_database']).resolve()
    if not database.is_relative_to(source.parent) or not database.is_file():raise ValueError('Snapshot must be inside the source report directory')
    cases=load_quality_cases(args.cases);output=args.output_parent.resolve()/uuid4().hex;output.mkdir(parents=True)
    target=output/'snapshot/knowledge.sqlite3';target.parent.mkdir()
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(target) as dst:src.backup(dst)
    options=original['options']
    report=QualityBenchmark(TraceReplay(target)).run(cases,options['runtime_mode'],options['table_retrieval'],options['modes'])
    report.update(eval_dataset=str(args.cases.resolve()),dataset_sha256=digest(args.cases.read_bytes()),
        source_report=str(source),source_report_sha256=digest(source.read_bytes()),
        isolated_database=str(target),execution_mode='frozen_trace_replay',new_model_calls=0,
        original_implementation_sha256=original.get('implementation_sha256'),
        model_latency_measurements_from_original_run=True,training_performed=False)
    QualityBenchmark.write(report,output)
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(output),'new_model_calls':0,'metrics':report['metrics'],'errors':report['execution_errors']},ensure_ascii=False))
    if report['execution_errors']:raise SystemExit(1)


if __name__=='__main__':main()
