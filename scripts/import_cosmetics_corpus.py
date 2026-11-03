"""Import canonical cosmetics images/captions and text training references locally."""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.knowledge.corpus_import import CosmeticsImporter,preflight
from app.storage.knowledge_store import KnowledgeStore


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',required=True)
    parser.add_argument('--store',default=str(ROOT/'data/runtime'))
    parser.add_argument('--run-dir',default=None)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--preflight-only',action='store_true')
    args=parser.parse_args()
    plan=preflight(Path(args.source_root))
    print(json.dumps({'images':len(plan['images']),'unique_training_files':len(plan['training']),'training_records':plan['training_records'],'image_bytes':plan['image_bytes']},ensure_ascii=False),flush=True)
    if args.preflight_only:return
    directory=Path(args.run_dir) if args.run_dir else ROOT/'data/runtime/corpus-imports'/uuid4().hex
    output=CosmeticsImporter(KnowledgeStore(args.store),directory).execute(plan,args.limit)
    print(json.dumps(output,ensure_ascii=False),flush=True)
    if output['failures']:raise SystemExit(1)


if __name__=='__main__':main()
