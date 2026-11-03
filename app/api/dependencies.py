from app.core.engine import ConfigurableRagEngine
from app.core.models import RagChunk


from app.storage.knowledge_store import KnowledgeStore
from app.storage.paths import runtime_dir
from app.knowledge.service import KnowledgeService

engine = ConfigurableRagEngine(KnowledgeStore(runtime_dir()))
service = KnowledgeService(engine)


def legacy_chunks_to_rag(items: list[dict]) -> list[RagChunk]:
    domain_map = {
        "wind_oam": "风电故障诊断",
        "load_forecast": "区域负荷预测",
        "storage_ems": "储能EMS",
        "calc_logic": "电气工程基础",
        "report_template": "电气工程基础",
    }
    chunks = []
    for item in items:
        domain = domain_map.get(item.get("domain"), item.get("domain", "电气工程基础"))
        metadata = {
            'document_id':item.get('document_id',item['chunk_id']),
            'knowledge_base_id':'electrical' if item.get('domain')=='calc_logic' else 'energy',
            "source": item.get("source_file", "demo"),
            "domain": domain,
            "task": item.get("scenario", "知识问答"),
            "doc_type": item.get("doc_type", "技术资料"),
            "chunk_type": "变量解释" if item.get("doc_type") == "variable_definition" else "故障规则" if item.get("doc_type") == "fault_rule" else "普通文本",
            "section": item.get("chunk_title", "未分节"),
            "fault_type": "塔架共振" if item.get("fault_type") == "tower_resonance" else item.get("fault_type", ""),
            "variables": item.get("variables", []),
        }
        chunks.append(RagChunk(doc_id=item.get("document_id", item["chunk_id"]), chunk_id=item["chunk_id"], content=item.get("chunk_content", ""), metadata=metadata))
    return chunks


def sync_legacy_document(document_id: str, items: list[dict]) -> None:
    """Keep legacy document processing and the configurable engine in sync."""
    chunks = legacy_chunks_to_rag(items)
    if chunks:
        if any(chunk.doc_id != document_id for chunk in chunks):
            raise ValueError("Chunks must belong to the requested document")
        engine.add_chunks(chunks)
    else:
        engine.remove_document(document_id)


def bootstrap_legacy_chunks(items: list[dict]) -> None:
    if engine.store.get_meta("initialized"):
        return
    grouped = {}
    for chunk in legacy_chunks_to_rag(items):
        grouped.setdefault(chunk.doc_id, []).append(chunk.model_dump())
    records = [({"document_id": doc_id, "title": chunks[0]["metadata"].get("source", doc_id),
                 'knowledge_base_id':chunks[0]['metadata']['knowledge_base_id'],
                 "domain": chunks[0]["metadata"]["domain"], "doc_type": chunks[0]["metadata"]["doc_type"]}, chunks)
               for doc_id, chunks in grouped.items()]
    engine.store.bootstrap_documents(records)
    engine.refresh()
