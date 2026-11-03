from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
from threading import Lock

from app.loaders.base_loader import LoadedDocument

_compile_lock = Lock()


class NativeVisionOcr:
    def __init__(self, directory):
        self.directory = Path(directory)

    def recognize(self, path):
        if platform.system() != 'Darwin':
            raise ValueError('Native OCR requires macOS; configure a platform OCR provider before importing scanned pages')
        source = Path(__file__).resolve().parents[2]/'scripts/local_model/vision_ocr.swift'
        key = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        tools = self.directory/'tools'; tools.mkdir(parents=True,exist_ok=True)
        binary = tools/('vision-ocr-'+key)
        with _compile_lock:
            if not binary.exists():
                cache = tools/'swift-module-cache';cache.mkdir(exist_ok=True)
                temporary = tools/('vision-build-'+os.urandom(8).hex())
                result = subprocess.run(['/usr/bin/xcrun','swiftc','-O','-module-cache-path',str(cache),str(source),'-o',str(temporary)],capture_output=True,timeout=120)
                if result.returncode:raise ValueError('Vision OCR compiler unavailable; install the Apple command line tools')
                try:os.link(temporary,binary)
                except FileExistsError:pass
        result = subprocess.run([str(binary),str(path)],capture_output=True,timeout=60)
        if result.returncode:raise ValueError('Local image recognition failed')
        payload=json.loads(result.stdout)
        lines=payload.get('lines',[])
        if not isinstance(lines,list):raise ValueError('Invalid OCR response')
        result={'text':'\n'.join(line['text'] for line in lines), 'regions':lines, 'engine':'apple_vision',
                'coverage':'recognized_text_only', 'table_structure':'not_inferred', 'visual_semantics':'not_inferred',
                'frame_count':payload.get('frame_count',1),'frames_processed':1}
        if result['frame_count']>1:result['warning']='Only the first image frame was recognized; split remaining frames before ingestion'
        return result


class RoutedLoader:
    """Transparent page-level native/OCR choice; originals are never rewritten."""

    def __init__(self, path, mode, directory):
        self.path,self.mode,self.directory=Path(path),mode,Path(directory)
        self.ocr=NativeVisionOcr(directory)

    def load(self, path):
        from app.loaders import get_loader
        suffix=self.path.suffix.lower()
        if suffix=='.pdf' and self.mode!='native':return self._pdf()
        if suffix in {'.png','.jpg','.jpeg','.webp','.tif','.tiff','.bmp','.gif','.avif'}:
            if self.mode=='native':raise ValueError('Image requires auto/OCR processing')
            return [self._image(self.path,{'page':1,'format':suffix.lstrip('.'),'route_reason':'image_input'})]
        if self.mode=='ocr' and suffix not in {'.pdf','.docx'}:
            raise ValueError('Forced OCR is only supported for PDF, DOCX image parts and image files')
        if suffix=='.docx':
            from app.loaders.docx_loader import DocxLoader
            loaded=DocxLoader(allow_empty=True).load(path)
        else:
            loaded=get_loader(path).load(path)
        for item in loaded:
            item.metadata.update(parse_route='native',route_reason='native_text_or_structured_document')
        if suffix=='.docx' and self.mode!='native':
            import zipfile
            with zipfile.ZipFile(path) as archive:
                images=[n for n in archive.namelist() if n.startswith('word/media/') and Path(n).suffix.lower() in {'.png','.jpg','.jpeg','.tif','.tiff','.bmp'}]
                if len(images)>100:raise ValueError('DOCX exceeds 100 image parts')
                for index,name in enumerate(images,1):
                    item=archive.getinfo(name)
                    if item.file_size>32*1024*1024:raise ValueError('DOCX image part exceeds 32 MiB')
                    image_path=self._derived(archive.read(name),Path(name).suffix)
                    loaded.append(self._image(image_path,{'format':'docx','section':f'嵌入图片 {index}', 'image_part':name,'route_reason':'embedded_image','position_scope':'media_part_not_page'}))
        if not loaded:raise ValueError('Document contains no processable content')
        return loaded

    def _derived(self, content, suffix):
        directory=self.directory/'derived';directory.mkdir(parents=True,exist_ok=True)
        path=directory/(hashlib.sha256(content).hexdigest()+suffix)
        if not path.exists():
            with path.open('xb') as f:f.write(content)
        elif hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(content).digest():
            raise ValueError('Derived image checksum mismatch')
        return path

    def _image(self, path, metadata):
        ocr=self.ocr.recognize(path)
        text=ocr.pop('text')
        metadata={**metadata,**ocr,'parser':'apple_vision','parse_route':'ocr','figure_id':'fig_'+hashlib.sha256(path.read_bytes()).hexdigest()[:24],
                  'image_path':str(path),'image_sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'review_status':'unreviewed'}
        if text.strip():
            from app.preprocessors.structured_tables import scan_table, to_html
            try:
                table = scan_table(path, ocr.get('regions', []))
                if table:
                    metadata.update(structured_tables=[table], table_structure='ruled_grid_pending_human')
                    text += '\n\n' + to_html(table)
                else:
                    from app.preprocessors.scan_recovery import deskew_candidate,borderless_candidate
                    candidate=deskew_candidate(path,ocr.get('regions',[]),self.directory/'derived') or borderless_candidate(path,ocr.get('regions',[]))
                    if candidate:metadata.update(table_candidates=[candidate],table_structure='candidate_requires_verification',table_candidates_not_indexed=[candidate['table_id']])
            except Exception as exc:
                metadata['table_warning'] = 'Scan grid unavailable: ' + type(exc).__name__
        if not text.strip():
            metadata['warning']='No readable text detected; image semantics require later visual review'
            # A retained figure record, explicitly not a description of its contents.
            text='[图片原件已保留；未识别出文字，视觉内容尚未解读。]'
            metadata['retrievable']=False
        return LoadedDocument(text,self.path.name,metadata)

    def _pdf(self):
        import fitz
        from app.preprocessors.structured_tables import page_tables, to_html, continuation_links, pdf_cell, explicit_continuation_tables
        result=[]
        all_tables=[]
        with fitz.open(self.path) as pdf:
            if pdf.needs_pass:raise ValueError('Encrypted PDF is not supported')
            if len(pdf)>300:raise ValueError('OCR route currently supports up to 300 PDF pages per document')
            for page_no,page in enumerate(pdf,1):
                text=page.get_text('text').strip(); images=len(page.get_images()); graphics=len(page.get_drawings())
                metadata={'format':'pdf','page':page_no,'page_count':len(pdf),'native_text_chars':len(text),'image_count':images,
                          'text_density':round(len(text)/max(1,page.rect.width*page.rect.height),6)}
                use_ocr=self.mode=='ocr' or (len(text)<40 and (images or graphics))
                if use_ocr:
                    if page.rect.width*page.rect.height*4>40_000_000:raise ValueError('PDF page exceeds OCR pixel budget')
                    image_path=self._derived(page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).tobytes('png'),'.png')
                    result.append(self._image(image_path,{**metadata,'route_reason':'forced_ocr' if self.mode=='ocr' else 'low_text_density_with_visual_content'}))
                elif text:
                    layout_text, _, runs, layout_flags = pdf_cell(page, page.rect)
                    geometry={'page':page_no,'runs':runs,'flags':layout_flags,'coordinate_system':'pdf_points_top_left',
                              'review_status':'source_checked_pending_human','formula_semantics':'not_inferred'}
                    geometry_path=self._derived(json.dumps(geometry,ensure_ascii=False).encode(),'.json')
                    metadata.update(text_geometry_path=str(geometry_path),text_geometry_sha256=hashlib.sha256(geometry_path.read_bytes()).hexdigest())
                    if any(run['script'] for run in runs):text=layout_text
                    tables=[]
                    if hasattr(page,'find_tables'):
                        try:
                            tables=page_tables(page)
                            for table in tables:
                                top=table['bbox'][1]
                                table['context_before']=page.get_text('text',clip=(0,max(0,top-100),page.rect.width,top))
                            all_tables.extend(tables)
                        except Exception as exc: metadata['table_warning']='Native table layout extraction unavailable: '+type(exc).__name__
                    metadata.update(parser='pymupdf',parse_route='native',route_reason='readable_text_layer',table_count=len(tables),coverage='text_layer_and_detected_tables',visual_content_processed=False)
                    metadata['structured_tables']=tables
                    if images:metadata['warning']='Text layer processed; embedded picture semantics require visual review'
                    blocked={'overlapping_cells','incomplete_grid','degenerate_cell','no_cell_text','sparse_grid_possible_diagram','unmapped_glyph'}
                    usable=[t for t in tables if not blocked.intersection(t['flags'])]
                    metadata['table_candidates_not_indexed']=[t['table_id'] for t in tables if t not in usable]
                    result.append(LoadedDocument(text+('\n\n'+'\n\n'.join(to_html(t) for t in usable) if usable else ''),self.path.name,metadata))
        links=continuation_links(all_tables)
        for item in result:
            item.metadata['table_continuations']=[link for link in links if item.metadata.get('page') in link['source_pages']]
        for table in explicit_continuation_tables(all_tables):
            result.append(LoadedDocument(to_html(table),self.path.name,{'format':'pdf','parse_route':'native',
                'parser':'pymupdf','section':table['table_id'],'source_pages':table['source_pages'],
                'structured_tables':[table],'review_status':'unreviewed','cross_page_view':True}))
        if not result:raise ValueError('PDF contains no processable content')
        return result
