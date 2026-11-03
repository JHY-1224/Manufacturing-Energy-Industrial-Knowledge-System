"""Normalized source declarations; unknown applicability remains unknown."""
from app.knowledge.catalog import KNOWLEDGE_BASES

def quality_profile(document):
    base=next((b for b in KNOWLEDGE_BASES if b['id']==document.get('knowledge_base_id')), {})
    kind=document.get('doc_type','general')
    evidence='image_reference' if document.get('image_sha256') else 'dataset_reference' if document.get('reference_type')=='dataset_archive' else 'technical_reference' if kind=='technical_source_reference' else 'text_reference'
    category=document.get('assigned_category') or document.get('category') or ''
    return {'industry':base.get('parent_id') or base.get('id'),'knowledge_base_id':base.get('id'),'manufacturing_type':base.get('industry_group') or ('discrete' if base.get('parent_id')=='consumer_goods' else 'cross_industry'),
            'evidence_type':evidence,'source_subcategory':category or None,'subcategory_status':'source_declared_not_visual_fact' if category else 'not_declared',
            'version':document.get('source_version') or None,'version_status':'declared' if document.get('source_version') else 'not_declared',
            'source_url':document.get('source_page') or document.get('source_url') or None,
            'applicability':{'materials':None,'equipment':None,'process_conditions':None,'status':'not_explicitly_verified'},
            'limitations':['仅按所引来源及版本使用','缺失材料、设备和工况时不得直接作为生产参数','AI证据审核不等于现场质量放行'],
            'normalization_version':'source_profile_v1','inferred_production_values':False}
