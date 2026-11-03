"""Bound MLX allocation without changing the pretrained observer or cache identity."""
import argparse
from pathlib import Path

def configure_limits(mx,memory_gib=10,cache_mib=128):
 if not 6<=memory_gib<=16 or not 0<=cache_mib<=1024:raise ValueError('Invalid local vision memory limits')
 mx.set_memory_limit(memory_gib*1024**3);mx.set_cache_limit(cache_mib*1024**2)
 return {'memory_limit_bytes':memory_gib*1024**3,'cache_limit_bytes':cache_mib*1024**2}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--embedding',type=Path,required=True);p.add_argument('--vision',type=Path,required=True);p.add_argument('--port',type=int,default=8083);p.add_argument('--memory-gib',type=int,default=10);p.add_argument('--cache-mib',type=int,default=128);a=p.parse_args()
 import mlx.core as mx
 limits=configure_limits(mx,a.memory_gib,a.cache_mib)
 from scripts.serve_local_vision_batch import observer_application
 app=observer_application(a.embedding.resolve(),a.vision.resolve());prior=next(r.endpoint for r in app.routes if getattr(r,'path',None)=='/health');app.router.routes=[r for r in app.routes if getattr(r,'path',None)!='/health']
 @app.get('/health')
 def health():return {**prior(),'inference_memory_limits':limits,'operational_limits_do_not_change_weights_prompt_or_preprocessing':True}
 import uvicorn
 uvicorn.run(app,host='127.0.0.1',port=a.port,workers=1)
if __name__=='__main__':main()
