"""Complete all decoded frames of small reference animations without replacing sources."""
import json,math,os,subprocess
from pathlib import Path
from uuid import uuid4
from app.storage.knowledge_store import digest

VERSION='all_decoded_frames_unique_text_mean_features_v1'

def analyze_frames(service,document):
    store=service.store;sha=document['source_sha256'];key=digest((service.key(sha)+'\0'+VERSION).encode())
    if digest(Path(document['local_path']).read_bytes())!=sha:raise ValueError('Animation original changed')
    with store.connection() as db:row=db.execute('SELECT payload FROM image_analysis_cache WHERE cache_key=?',(key,)).fetchone()
    if row:return key,json.loads(row[0]),True
    source=Path(__file__).resolve().parents[2]/'scripts/local_model/split_image_frames.swift'
    tools=store.directory/'tools';tools.mkdir(exist_ok=True);binary=tools/('image-frames-'+digest(source.read_bytes())[:16])
    if not binary.exists():
        from app.knowledge.image_analysis import _compile_lock
        with _compile_lock:
            if not binary.exists():
                temp=tools/('frames-build-'+uuid4().hex)
                result=subprocess.run(['/usr/bin/xcrun','swiftc','-O','-module-cache-path',str(tools/'swift-module-cache'),str(source),'-o',str(temp)],capture_output=True,timeout=120)
                if result.returncode:raise ValueError(result.stderr.decode(errors='replace')[-1000:])
                try:os.link(temp,binary)
                except FileExistsError:pass
    root=store.directory/'derived';root.mkdir(exist_ok=True);manifest=root/(sha+'-frames.json')
    if manifest.exists():frames=json.loads(manifest.read_text())['frames']
    else:
        directory=root/('animation-'+sha[:16]+'-'+uuid4().hex);directory.mkdir()
        result=subprocess.run([str(binary),document['local_path'],str(directory)],capture_output=True,timeout=120)
        if result.returncode:raise ValueError('Animation frame extraction failed')
        frames=json.loads(result.stdout)['frames']
        for f in frames:f['sha256']=digest(Path(f['file']).read_bytes())
        temp=root/('frames-manifest-'+uuid4().hex+'.json');temp.write_text(json.dumps({'frames':frames},ensure_ascii=False))
        try:os.link(temp,manifest)
        except FileExistsError:pass
    if len(frames)!=document['frame_count']:raise ValueError('Animation frame count changed')
    results=[];seen=set();extra=[]
    first_key,first,_=service.analyze(document['local_path'],sha)
    seen.update(l['text'] for l in first['lines'])
    for f in frames:
        path=Path(f['file']).resolve()
        if not path.is_relative_to(root.resolve()) or digest(path.read_bytes())!=f['sha256']:raise ValueError('Frame checksum mismatch')
        _,result,_=service.analyze(path,f['sha256'])
        fresh=[]
        for line in result['lines']:
            if line['text'] not in seen:seen.add(line['text']);fresh.append(line['text'])
        if fresh:extra.append('【第 '+str(f['frame_index'])+'/'+str(len(frames))+' 帧 OCR】\n'+'\n'.join(fresh))
        results.append({**f,'analysis_key':service.key(f['sha256']),'ocr_characters':len(result['text']),'vector':result['vector']})
    vector=[sum(r['vector'][i] for r in results)/len(results) for i in range(first['dimension'])]
    norm=math.sqrt(sum(v*v for v in vector))
    if norm<=0:raise ValueError('Invalid combined frame vector')
    additional='\n\n'.join(extra)
    result={**first,'source_sha256':sha,'first_frame_analysis_key':first_key,'first_frame_text':first['text'],
            'text':first['text']+('\n\n'+additional if additional else ''),'additional_ocr_text':additional,
            'frames_processed':len(frames),'frame_count':len(frames),'frame_analysis':results,
            'frame_aggregation':VERSION,'vector':[v/norm for v in vector]}
    store.save_asset(store.encode(result).encode(),'animation-analysis.json')
    with store.connection() as db:db.execute('INSERT OR IGNORE INTO image_analysis_cache VALUES (?,?,?,?)',(key,sha,service.analyzer.fingerprint,store.encode(result)))
    return key,result,False
