"""Convert an existing official Qwen3-VL checkpoint into a new local inference copy.
No training, calibration, adapters, remote code, source overwrite or network use.
"""
import argparse,hashlib,json,os,shutil,time
from pathlib import Path
from importlib.metadata import version

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 source=a.source.resolve();out=a.output.resolve()
 if out.exists() or source==out or source in out.parents:raise ValueError('Output must be a new directory outside the original')
 config=json.loads((source/'config.json').read_text());assert config['model_type']=='qwen3_vl' and not config.get('quantization')
 index=source/'model.safetensors.index.json';names=set(json.loads(index.read_text())['weight_map'].values());before={n:((source/n).stat().st_size,(source/n).stat().st_mtime_ns) for n in names}
 os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
 import mlx.core as mx
 from mlx.utils import tree_flatten
 from mlx_vlm.utils import fetch_from_hub,save_weights,save_config,skip_multimodal_module
 from mlx_vlm.quant_utils import quantize_model
 mx.set_default_device(mx.cpu);mx.set_cache_limit(64*1024**2);start=time.monotonic()
 model,config,processor=fetch_from_hub(source,lazy=True,trust_remote_code=False)
 model_predicate=getattr(model,'quant_predicate',lambda p,m:True)
 model,config=quantize_model(model,config,64,4,mode='affine',quant_predicate=lambda p,m:not skip_multimodal_module(p) and model_predicate(p,m))
 leaves=tree_flatten(model.leaf_modules(),is_leaf=lambda x:hasattr(x,'parameters'))
 for i,(_,module) in enumerate(leaves,1):
  mx.eval(module.parameters());mx.clear_cache()
  if i%50==0:print('evaluated',i,'/',len(leaves),flush=True)
 out.mkdir(parents=True);save_weights(out,model,donate_weights=True)
 for pattern in ['*.json','*.txt','*.jinja','*.model']:
  for f in source.glob(pattern):
   if f.name not in {'model.safetensors.index.json','config.json'}:shutil.copy2(f,out/f.name)
 processor.save_pretrained(out);save_config(config,config_path=out/'config.json')
 assert all(((source/n).stat().st_size,(source/n).stat().st_mtime_ns)==v for n,v in before.items())
 receipt={'source_model':'Qwen/Qwen3-VL-8B-Instruct','source_directory':str(source),'source_index_sha256':hashlib.sha256(index.read_bytes()).hexdigest(),'source_weights_stat_unchanged':True,'source_weights':before,'training_performed':False,'adapters_applied':False,'quantization':{'bits':4,'group_size':64,'mode':'affine','vision_preserved':True},'elapsed_seconds':time.monotonic()-start,'versions':{m:version(m) for m in ['mlx','mlx-vlm','transformers','Pillow']},'status':'converted_requires_inference_validation'}
 (out/'conversion.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2));print(json.dumps(receipt,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
