"""Source-bound real recovery checks; derived tables never replace original chunks."""
import argparse,json,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.storage.knowledge_store import KnowledgeStore,digest
from app.core.engine import ConfigurableRagEngine
from app.core.models import QueryRequest,QueryOptions
from app.knowledge.structure_resolution import StructureResolutions
from scripts.run_local_stack import local_environment
CASES=[
 ('basf_ultramid__p0048_table01','materials','Ultramid Table 8 A3K 1.5 mm spiral length flow characteristics', ['A3K','385','257']),
 ('doe_air_protocol__p0030_table01','energy','Compressed Air CFM-bin Hour Profile Table 6 CFM-bin 1 Base Case Hours per Year', ['324','2,640']),
 ('doe_air_protocol__p0033_table01','energy','Table 9 Example Project Compressed-Air CFM-Bin Hour CFM-bin 1 Input Power Base Case Post Case', ['63.5','60.1']),
 ('doe_electrolysis__p0052_table01','energy','LA electrolyzer Table A-2 System electricity usage average 2031 target', ['50.4 kWh/kg','54.3 kWh/kg']),
 ('doe_electrolysis__p0054_table01','energy','Table A-4 Financial Assumptions Real return on equity Debt equity', ['10.89%','0.62'])]

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();r=a.root;os.environ.update(local_environment());store=KnowledgeStore(r/'snapshot');store.asset_dir=(Path.cwd()/'data/runtime/originals').resolve();service=StructureResolutions(store);values=service.current();engine=ConfigurableRagEngine(store);results=[]
 frozen=[dict(candidate_id=i,knowledge_base_id=b,question=q,required_cells=v) for i,b,q,v in CASES];path=r/'recovered-table-cases.json'
 if not path.exists():path.write_text(json.dumps(frozen,ensure_ascii=False,indent=2))
 elif json.loads(path.read_text())!=frozen:raise ValueError('Frozen table cases changed')
 for case in frozen:
  start=time.monotonic();trace=engine.query(QueryRequest(question=case['question'],generate_answer=False,options=QueryOptions(runtime_mode='configured',metadata_filter={'knowledge_base_id':case['knowledge_base_id']},retrieval_mode='hybrid',table_retrieval=True,evidence_retrieval=True,top_k=18,final_top_k=6,rerank=True,compression=True)))
  hits=trace.reranked_docs;target=next((h for h in hits if h.metadata.get('structure_resolution_id')==case['candidate_id']),None);results.append({'candidate_id':case['candidate_id'],'passed':target is not None and all(v in target.content for v in case['required_cells']),'rank':next((i+1 for i,h in enumerate(hits) if h is target),None),'trace_id':trace.trace_id,'evidence_revision_checked':trace.runtime.get('evidence_revision_checked'),'elapsed_ms':round((time.monotonic()-start)*1000,2),'hits':[h.model_dump() for h in hits]});print(case['candidate_id'],results[-1]['passed'],results[-1]['rank'],flush=True)
  (r/'recovered-table-results.partial.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
 previews=[]
 for v in values:
  data=service.preview(v['document_id'],v['candidate_id']);previews.append({'candidate_id':v['candidate_id'],'png_sha256':digest(data),'bytes':len(data)});assert data.startswith(b'\x89PNG')
 (r/'structure-preview-verification79.json').write_text(json.dumps(previews,ensure_ascii=False,indent=2))
 result={'cases':len(results),'passed':sum(v['passed'] for v in results),'previews_verified':len(previews),'results':results};(r/'recovered-table-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print('final',result['passed'],result['previews_verified'],flush=True)
if __name__=='__main__':main()
