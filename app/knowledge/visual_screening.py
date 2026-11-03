"""Explicitly scoped AI checks. Structural screening does not certify visual facts."""
import re
from app.storage.knowledge_store import now

def screen(value,document):
 if value.get('source_sha256')!=document.get('source_sha256') or value.get('revision_id')!=document.get('revision_id'):raise ValueError('Observation source is stale')
 ob=value.get('observation') or {};text=ob.get('visible_description','');flags=[]
 if not isinstance(text,str) or not text.strip() or len(text)>600:flags.append('invalid_description')
 if re.search(r'导致|根因|判定合格|质量合格|确认缺陷|故障原因|必须设置|应设为',text):flags.append('unsupported_causal_or_prescriptive_claim')
 if re.search(r'\d+(?:\.\d+)?\s*(?:MPa|kW|kWh|rpm|°C|℃|毫[米升]|千[瓦克]|摄氏|伏特)',text,re.I):flags.append('unverified_technical_number')
 if re.search(r'[A-Z][A-Z0-9_-]{1,}',text):flags.append('model_text_not_verified_ocr')
 # Model-generated tiny text and exact engineering numbers are not search keys.
 retrieval_text=re.sub(r'\b[A-Z][A-Z0-9_-]{1,}\b|\d+(?:\.\d+)?',' ',text)
 prohibited={'invalid_description','unsupported_causal_or_prescriptive_claim','unverified_technical_number'}
 value={**value,'review_status':'ai_source_screened','ai_screening':{'reviewer':'Codex AI / local boundary checks','checked_at':now(),'scope':'source_identity_schema_unknowns_and_claim_boundary','source_revision_verified':True,'source_sha256_verified':True,'flags':flags,'retrieval_allowed':not bool(prohibited&set(flags)),'retrieval_text':retrieval_text,'all_visible_claims_independently_verified':False,'technical_fact_approved':False,'human_approved':False}}
 return value
