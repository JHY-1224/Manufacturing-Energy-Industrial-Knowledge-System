from __future__ import annotations

from collections import Counter
from pathlib import Path

from app.core.models import EnergyRelation, RagChunk
from app.storage.review_store import available
from app.knowledge.catalog import base_policy

from .energy_schema import normalize_domain
from .entity_extractor import EntityExtractor
from .graph_store import GraphStore
from .relation_extractor import RELATION_RULES, RelationExtractor, relation_id


class GraphIndexBuilder:
    def __init__(self, store: GraphStore | None = None) -> None:
        self.store = store or GraphStore()
        self.entity_extractor = EntityExtractor()
        self.relation_extractor = RelationExtractor()

    def build_graph_from_chunks(
        self,
        chunks: list[RagChunk],
        domain: str | None = None,
        rebuild: bool = True,
        persist: bool = True,
        knowledge_base_id: str | None = None,
        review_mode: str = 'reference',
    ) -> dict:
        if rebuild:
            self.store.entities.clear()
            self.store.relations.clear()
        normalized_domain = normalize_domain(domain) if domain else None
        for chunk in chunks:
            if not available(chunk.metadata, review_mode):
                continue
            base = chunk.metadata.get('knowledge_base_id')
            if knowledge_base_id and base != knowledge_base_id:
                continue
            if (chunk.metadata.get('exclusive_to_system') or base_policy(base)) and base != knowledge_base_id:
                continue
            chunk_domain = normalize_domain(str(chunk.metadata.get("domain", "")))
            if normalized_domain and chunk_domain != normalized_domain:
                continue
            entities = self.entity_extractor.extract(chunk)
            for entity in entities:
                self.store.add_entity(entity)
            for relation in self.relation_extractor.extract(chunk, entities):
                self.store.add_relation(relation)
        self._add_cross_chunk_rules()
        if persist:
            self.store.save()
        return self.stats()

    def _add_cross_chunk_rules(self) -> None:
        by_name: dict[str, list] = {}
        for entity in self.store.entities.values():
            by_name.setdefault(entity.name.lower(), []).append(entity)
        for source_name, target_name, relation_type, rule_domain in RELATION_RULES:
            sources = by_name.get(source_name.lower(), [])
            targets = by_name.get(target_name.lower(), [])
            for source in sources:
                for target in targets:
                    if rule_domain != "cross_domain" and source.domain != rule_domain:
                        continue
                    chunk_ids = list(dict.fromkeys(source.source_chunk_ids + target.source_chunk_ids))
                    relation = EnergyRelation(
                        relation_id=relation_id(source.entity_id, target.entity_id, relation_type),
                        source_entity=source.entity_id,
                        target_entity=target.entity_id,
                        relation_type=relation_type,
                        domain="cross_domain" if rule_domain == "cross_domain" else source.domain,
                        description=f"{source.name} {relation_type} {target.name}",
                        source_chunk_ids=chunk_ids,
                        confidence=0.9,
                        metadata={"rule": "energy_schema"},
                    )
                    self.store.add_relation(relation)

    def stats(self) -> dict:
        entity_types = Counter(item.entity_type for item in self.store.entities.values())
        relation_types = Counter(item.relation_type for item in self.store.relations.values())
        return {
            **self.store.status(),
            "domains": sorted({item.domain for item in self.store.entities.values()}),
            "top_entity_types": dict(entity_types.most_common()),
            "top_relation_types": dict(relation_types.most_common()),
        }


def build_graph_from_chunks(chunks: list[RagChunk], path: str | Path = "data/graph/energy_graph.json") -> dict:
    return GraphIndexBuilder(GraphStore(path)).build_graph_from_chunks(chunks)
