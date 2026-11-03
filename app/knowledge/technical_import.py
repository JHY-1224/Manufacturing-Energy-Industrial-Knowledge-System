"""Import a frozen, locally reviewed technical collection through the shared lifecycle."""
import json
from pathlib import Path

from app.knowledge.catalog import validate_base
from app.knowledge.corpus_import import CosmeticsImporter, contained
from app.storage.knowledge_store import digest


def preflight(root, records):
    root = Path(root).resolve(); plan = []; seen = set()
    for record in records:
        validate_base(record['knowledge_base_id'])
        if record['id'] in seen:
            raise ValueError('Duplicate technical source ID')
        seen.add(record['id'])
        original = contained(root, record['file'])
        prepared = contained(root, record['prepared_file'])
        if digest(original.read_bytes()) != record['sha256'] or digest(prepared.read_bytes()) != record['prepared_sha256']:
            raise ValueError('Technical original or prepared evidence changed')
        data = json.loads(prepared.read_text())
        if data['source_sha256'] != record['sha256'] or data['knowledge_base_id'] != record['knowledge_base_id']:
            raise ValueError('Prepared source provenance mismatch')
        if not data['chunks'] or any(not isinstance(c['content'], str) or not c['content'].strip() for c in data['chunks']):
            raise ValueError('Empty technical evidence')
        if any(any(ord(ch) < 32 and ch not in '\n\r\t' for ch in c['content']) for c in data['chunks']):
            raise ValueError('Unmapped control glyphs require source-page review before embedding')
        if max(len(c['content'].encode()) for c in data['chunks']) > 40 * 1024:
            raise ValueError('Oversized evidence requires structure-aware review')
        plan.append({'record': record, 'original': original, 'prepared': prepared, 'data': data})
    return plan


class TechnicalImporter(CosmeticsImporter):
    def import_item(self, item):
        record = item['record']; raw = item['original'].read_bytes()
        if digest(raw) != record['sha256'] or digest(item['prepared'].read_bytes()) != record['prepared_sha256']:
            raise ValueError('Technical source changed after preflight')
        metadata = {'title': record['title_zh'], 'knowledge_base_id': record['knowledge_base_id'],
            'domain': record['industry_name'], 'doc_type': 'technical_source_reference',
            'reference_type': record['reference_type'], 'source_title': record['title'],
            'source_page': record['resolved_url'], 'source_url': record['resolved_url'],
            'source_publisher': record['publisher'], 'source_version': record['version'],
            'source_record_id': record['id'], 'corpus_collection': 'professional-text-20261102-v1',
            'source_manifest_sha256': record['prepared_sha256'], 'source_project_path': str(item['original']),
            'prepared_evidence_path': str(item['prepared']), 'source_downloaded_at': record['downloaded_at'],
            'keywords': record['topics'], 'review_status': 'primary_source_selected_engineering_claims_not_approved',
            'license_status': record['license_status'], 'public_release_approved': False,
            'evidence_kind': 'source_text', 'machine_table_candidate_count': record['machine_table_candidates'],
            'checked_table_count': record['checked_tables'], 'source_language': record['language']}
        contents = []
        for chunk in item['data']['chunks']:
            # Catalog title is editorial indexing context, not a translation of facts.
            prefix = '【资料目录标题】' + record['title_zh'] + '\n'
            contents.append((prefix + chunk['content'], chunk['metadata']))
        return self._commit('corpus:technical:v1:' + record['id'] + ':' + record['sha256'],
            record['title_zh'] + item['original'].suffix, raw, metadata, contents,
            policy={'allow_cloud_upload': False, 'allow_public_redistribution': False, 'allow_training': False,
                    'data_source': 'local_technical_reference_collection'}, id_prefix='doc_technical_',
            provenance={'operation': 'technical_reference_import', 'parser': 'reviewed_technical_collection_v1',
                'route': 'source_pages_tables_figures',
                'warnings': ['原发布方专业参考；文档版本、来源、表格核对范围分别保留，未作为企业批准工艺或已训练模型结论。']})
