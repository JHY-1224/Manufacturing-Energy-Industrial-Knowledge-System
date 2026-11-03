"""Static first-frame raster for OCR overlays; no model inference or source editing."""
import argparse,hashlib,io,os,subprocess,sys
from pathlib import Path
from uuid import uuid4

def raster(raw,expected_sha256):
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:raise ValueError('Image source checksum changed')
    from PIL import Image,PngImagePlugin
    Image.MAX_IMAGE_PIXELS=80_000_000
    with Image.open(io.BytesIO(raw)) as image:
        if image.width*image.height>80_000_000:raise ValueError('Image exceeds preview pixel budget')
        image.seek(0)
        # Apple Vision's stored coordinates refer to the raw first-frame raster.
        # Do not apply EXIF rotation independently from that OCR input.
        frame=image.convert('RGBA' if 'A' in image.getbands() else 'RGB');frame.thumbnail((1600,1600))
        metadata=PngImagePlugin.PngInfo();metadata.add_text('source_sha256',expected_sha256)
        output=io.BytesIO();frame.save(output,format='PNG',pnginfo=metadata);data=output.getvalue()
        if len(data)>8*1024*1024:raise ValueError('Preview exceeds byte budget')
        return data

def source_preview(store,document_id,revision_id=None):
    from app.storage.knowledge_store import KnowledgeConflictError
    document=store.get_document(document_id)
    if not document:raise FileNotFoundError('Image document not found')
    if not document.get('image_sha256'):raise ValueError('Select an image document')
    if revision_id and revision_id!=document.get('revision_id'):raise KnowledgeConflictError('Image revision changed')
    source=Path(document['local_path']).resolve()
    if source.parent!=store.asset_dir.resolve() or not source.is_file():raise FileNotFoundError('Original is unavailable')
    raw=source.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    if sha!=document['source_sha256']:raise KnowledgeConflictError('Original image checksum changed')
    directory=store.directory/'image-preview-cache';path=directory/(sha+'-first-frame-v1.png')
    def verified(data):
        if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data)>8*1024*1024 or b'source_sha256\x00'+sha.encode() not in data:raise ValueError('Invalid source-bound first-frame preview')
        current=store.get_document(document_id)
        if not current or current.get('write_version')!=document.get('write_version'):raise KnowledgeConflictError('Image changed while previewing')
        return data
    if path.is_symlink():raise ValueError('Preview cache cannot be a symbolic link')
    if path.exists():
        if path.stat().st_size>8*1024*1024:raise ValueError('Preview exceeds byte budget')
        return verified(path.read_bytes())
    try:data=raster(raw,sha)
    except ImportError:
        interpreter=Path(__file__).resolve().parents[2]/'.venv-vision/bin/python'
        if not interpreter.is_file():raise ValueError('Image preview needs Pillow; install backend/requirements-parsers.txt')
        result=subprocess.run([str(interpreter),'-B',str(Path(__file__).resolve()),'--source',str(source),'--sha256',sha],capture_output=True,timeout=30)
        if result.returncode:raise ValueError('Static first-frame preview failed; inspect the original')
        data=result.stdout
    except OSError as exc:raise ValueError('Cannot decode the current image first frame') from exc
    verified(data)
    directory.mkdir(parents=True,exist_ok=True);temporary=directory/('staging-'+uuid4().hex+'.png');temporary.write_bytes(data)
    try:os.link(temporary,path)
    except FileExistsError:pass
    return data

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--sha256',required=True);args=parser.parse_args()
    sys.stdout.buffer.write(raster(args.source.read_bytes(),args.sha256))
