"""Record explicit Codex source-image decisions, retaining every model original."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.storage.knowledge_store import KnowledgeStore,digest
from app.storage.evidence_review_store import EvidenceReviewStore

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();r=a.root;store=KnowledgeStore(r/'snapshot');review=EvidenceReviewStore(store)
 path=r/'spot48-ai-source-review.json'
 if path.exists():raise FileExistsError('Preserve existing AI decisions')
 decisions=json.loads((r/'spot48-source-decisions.json').read_text());models={v['document_id']:v for v in json.loads((r/'spot48-model-before-review.json').read_text())};accepted={3,4,11,12,15,19,22,23,25,28,31,36,40,41,43,45};out=[]
 for i,decision in enumerate(decisions,1):
  identity=decision['document_id'];value=models[identity];d=store.get_document(identity)
  if (d['revision_id'],d['source_sha256'])!=(decision['revision_id'],decision['source_sha256']):raise ValueError('Reviewed source changed')
  evidence={'fingerprint':value['fingerprint'],'source_sha256':d['source_sha256'],'source_inspection':decision['inspection'],'model_observation_sha256':digest(store.encode(value['observation']).encode()),'inspection_scope':'first_frame_visible_objects_and_layout','technical_fact_approved':False}
  if i not in accepted:evidence['corrected_observation']={'visible_description':decision['source_observation'],'visible_text':[],'regions':[],'unknowns':['具体型号、材质、工况、数值及工艺适用性未由该描述确认','不能由静态画面确定缺陷原因或质量结论']}
  out.append(review.append(identity,d['revision_id'],'accepted' if i in accepted else 'corrected','visual_observation',decision['note']+('模型可见内容描述在本审核范围内接受。' if i in accepted else '采用已查看原图的保守描述；模型原文完整保留。'),evidence))
 summary={'count':len(out),'accepted':len(accepted),'corrected':len(out)-len(accepted),'scope':'48 stratified source-image checks, first frames; no human gold or industrial accuracy claim','decisions':out}
 path.write_text(json.dumps(summary,ensure_ascii=False,indent=2));print('source reviews',summary['count'],summary['accepted'],summary['corrected'])
if __name__=='__main__':main()
