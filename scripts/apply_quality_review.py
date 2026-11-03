"""Apply explicit, fingerprint-bound human or authorized AI decisions to a NEW evaluation dataset."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def apply(cases_path, decisions_path, output):
    from app.evaluation.quality_benchmark import load_quality_cases
    from app.storage.knowledge_store import digest
    cases=load_quality_cases(cases_path);decisions=json.loads(Path(decisions_path).read_text())
    if decisions.get('dataset_sha256')!=digest(Path(cases_path).read_bytes()):raise ValueError('Reviewed dataset fingerprint changed')
    reviewer=decisions.get('reviewer','').strip()
    if not reviewer or not decisions.get('reviewed_at'):raise ValueError('Human reviewer attribution and date are required')
    kind=decisions.get('review_kind','human_review')
    if kind not in {'human_review','ai_source_review'}:raise ValueError('Unknown review kind')
    status='ai_source_reviewed' if kind=='ai_source_review' else 'human_reviewed'
    known={c.query_id:c for c in cases};seen=set();approved=[]
    for decision in decisions.get('decisions',[]):
        identity=decision.get('query_id')
        if identity not in known or identity in seen:raise ValueError('Unknown or duplicate reviewed case')
        seen.add(identity)
        if decision.get('decision') not in {'approved','rejected'} or not decision.get('note','').strip():raise ValueError('Explicit decision and note required')
        if decision['decision']=='approved':
            if decision.get('verified_source') is not True:raise ValueError('Source verification acknowledgement required')
            approved.append(known[identity].model_copy(update={'review_status':status,'reviewer':reviewer}))
    if not approved:raise ValueError('No explicitly approved cases; no approved dataset was created')
    output=Path(output);receipt=output.with_suffix(output.suffix+'.review.json')
    if output.exists() or receipt.exists():raise FileExistsError('Review output must be new; previous files are preserved')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as handle:handle.write(''.join(c.model_dump_json()+'\n' for c in approved))
    with receipt.open('x') as handle:handle.write(json.dumps({**decisions,'source_dataset':str(Path(cases_path).resolve()),'decisions_sha256':digest(Path(decisions_path).read_bytes()),'approved_dataset_sha256':digest(output.read_bytes()),'approved_count':len(approved)},ensure_ascii=False,indent=2))
    return {'approved_cases':len(approved),'output':str(output.resolve()),'original_dataset_preserved':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cases',type=Path,required=True);parser.add_argument('--decisions',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(apply(args.cases,args.decisions,args.output),ensure_ascii=False))
