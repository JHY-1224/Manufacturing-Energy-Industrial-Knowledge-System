import hashlib


class Deduplicator:
    def deduplicate(self, hits: list) -> list:
        seen: set[str] = set()
        unique = []
        seen_content = set()
        for hit in hits:
            key = ('image-context:' + hit.doc_id) if hit.metadata.get('image_context_recovered') else (hit.chunk_id or hashlib.sha1(hit.content.encode("utf-8")).hexdigest())
            content_key = (hit.doc_id, hashlib.sha256(hit.content.encode('utf-8')).hexdigest())
            if key in seen or content_key in seen_content:
                continue
            seen.add(key)
            seen_content.add(content_key)
            unique.append(hit)
        return unique
