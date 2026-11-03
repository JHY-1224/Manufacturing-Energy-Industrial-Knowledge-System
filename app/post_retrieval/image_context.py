"""Recover available OCR evidence when a short image caption wins retrieval."""
from app.embeddings.embedding_factory import tokenize


class ImageContextRecover:
    def recover(self, query, hits, scoped_chunks, max_characters=4800):
        wanted={h.doc_id for h in hits if h.metadata.get('image_sha256')}
        groups={identity:[] for identity in wanted};terms=set(tokenize(query))
        for chunk in scoped_chunks:
            if chunk.doc_id in groups and chunk.metadata.get('chunk_kind') in {'source_caption','image_ocr','image_frame_ocr'}:
                groups[chunk.doc_id].append(chunk)
        contexts={}
        for identity,chunks in groups.items():
            captions=[c for c in chunks if c.metadata.get('chunk_kind')=='source_caption']
            ocr=[c for c in chunks if c.metadata.get('chunk_kind')!='source_caption']
            if not ocr:continue
            ocr.sort(key=lambda c:(-len(terms & set(tokenize(c.content))),c.chunk_id))
            selected=[];parts=[];size=0;omitted=[]
            for chunk in captions[:1]+ocr:
                part='【来源片段 '+chunk.chunk_id+'】\n'+chunk.content
                separator=2 if parts else 0
                if size+separator+len(part)>max_characters:
                    omitted.append(chunk.chunk_id);continue
                parts.append(part);selected.append(chunk.chunk_id);size+=separator+len(part)
            if selected:contexts[identity]=('\n\n'.join(parts),selected,omitted)
        for hit in hits:
            if hit.doc_id in contexts:
                content,ids,omitted=contexts[hit.doc_id]
                hit.content=content
                hit.metadata={**hit.metadata,'image_context_recovered':True,'evidence_kind':'image_text_context','evidence_chunk_ids':ids,
                              'image_context_scope':'caption_and_machine_ocr_not_visual_reasoning',
                              'image_context_omitted_chunk_ids':omitted}
        return hits
