"""Validate and import a frozen local technical collection; keep an independent DB backup."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.knowledge.technical_import import TechnicalImporter, preflight
from app.storage.knowledge_store import KnowledgeStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', required=True)
    parser.add_argument('--store', default=str(ROOT / 'data/runtime'))
    parser.add_argument('--run-dir', required=True)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args(); root = Path(args.collection).resolve(); run = Path(args.run_dir).resolve()
    records = [json.loads(line) for line in (root / 'manifest.jsonl').read_text().splitlines() if line.strip()]
    plan = preflight(root, records); run.mkdir(parents=True, exist_ok=True)
    print(json.dumps({'validated_sources': len(plan), 'chunks': sum(len(x['data']['chunks']) for x in plan)}), flush=True)
    if args.preflight_only: return
    current = Path(args.store).resolve() / 'knowledge.sqlite3'; backup = run / 'pre-text-import.sqlite3'
    if current.exists() and not backup.exists():
        with sqlite3.connect('file:' + str(current) + '?mode=ro', uri=True) as source, sqlite3.connect(backup) as destination:
            source.backup(destination)
    if backup.exists():
        with sqlite3.connect('file:' + str(backup) + '?mode=ro', uri=True) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise ValueError('Backup integrity failed')
    importer = TechnicalImporter(KnowledgeStore(args.store), run); results = []
    for item in plan:
        result = {'source_id': item['record']['id'], **importer.import_item(item)}
        results.append(result)
        with (run / 'import-progress.jsonl').open('a') as handle: handle.write(json.dumps(result, ensure_ascii=False) + '\n')
        print(json.dumps(result, ensure_ascii=False), flush=True)
    report = {'sources': results, 'imported': sum(r['status'] == 'imported' for r in results),
              'reused': sum(r['status'] == 'reused' for r in results), 'backup': str(backup), 'status': 'completed'}
    (run / ('import-result-' + uuid4().hex + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
