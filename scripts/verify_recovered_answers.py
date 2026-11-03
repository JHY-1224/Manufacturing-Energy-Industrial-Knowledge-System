"""Actual local answers for source-restored evidence, not a training/eval gold claim."""
import argparse,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.storage.knowledge_store import KnowledgeStore
from app.knowledge.structure_resolution import StructureResolutions
from app.core.engine import ConfigurableRagEngine
from app.core.models import QueryRequest,QueryOptions
from scripts.run_local_stack import local_environment
CASES=[
 ('basf_ultramid__p0048_table01','materials','Ultramid Table 8中，A3K在1.5 mm螺旋厚度下的螺旋流长和比值i分别是多少？',['385','257']),
 ('doe_air_protocol__p0030_table01','energy','Table 6 Compressed Air CFM-bin Hour Profile中，CFM-bin 1的负荷和Base Case年小时数是多少？',['324','2,640']),
 ('doe_air_protocol__p0033_table01','energy','Table 9 Example Project Compressed-Air中，CFM-bin 1的Base Case和Post Case输入功率分别是多少kW？',['63.5','60.1']),
 ('doe_electrolysis__p0052_table01','energy','Table A-2 LA electrolyzer的2031 Target，System electricity usage (average)为多少？请保留单位。',['50.4','kWh/kg']),
 ('doe_electrolysis__p0054_table01','energy','Table A-4 Financial Assumptions中Real return on equity是多少？',['10.89']),
 ('ahss_flc__html_table01','automotive','FLC文中的Equation 1是什么公式？两个输入变量是什么？不要代入计算。',['23.3','14.13','0.21'])]

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--ids',nargs='*');a=p.parse_args();r=a.root
 if a.output.exists():raise FileExistsError('Keep previous answer evidence')
 a.output.mkdir(parents=True);os.environ.update(local_environment());os.environ['RAG_MODEL_MAX_OUTPUT_TOKENS']='512';store=KnowledgeStore(r/'snapshot');resolutions=StructureResolutions(store);values={v['candidate_id']:v for v in resolutions.current()};engine=ConfigurableRagEngine(store);results=[]
 (a.output/'frozen-questions.json').write_text(json.dumps(CASES,ensure_ascii=False,indent=2))
 for identity,base,question,expected in CASES:
  if a.ids and identity not in a.ids:continue
  v=values[identity];trace=engine.query(QueryRequest(question=question,generate_answer=True,options=QueryOptions(runtime_mode='configured',metadata_filter={'knowledge_base_id':base,'document_id':v['document_id']},retrieval_mode='hybrid',top_k=12,final_top_k=3,table_retrieval=True,evidence_retrieval=True,cross_language_query=True,rerank=True,compression=True)))
  answer=trace.answer;fragments=all(fragment in answer.replace(',','') or fragment.replace(',','') in answer.replace(',','') for fragment in expected);sources=[h.chunk_id for h in trace.reranked_docs];citation=any(source in answer for source in sources);value={'candidate_id':identity,'question':question,'passed_fragments_and_citation':fragments and citation,'answer':answer,'source_ids':sources,'trace_id':trace.trace_id,'contexts':trace.compressed_context,'runtime':trace.runtime,'evidence_revision_checked':trace.runtime.get('evidence_revision_checked'),'limitation':'fragment/citation checks require source support review; not industrial factual accuracy'};results.append(value);(a.output/'partial.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(identity,fragments,citation,flush=True)
 result={'cases':len(results),'passed_fragments_and_citation':sum(v['passed_fragments_and_citation'] for v in results),'results':results};(a.output/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
