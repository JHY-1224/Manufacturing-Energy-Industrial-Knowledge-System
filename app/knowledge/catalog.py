"""Product domains are configuration, never evidence or fabricated knowledge."""

KNOWLEDGE_BASES = [
    {"id": "automotive", "name": "汽车制造", "industry_group": "discrete", "primary_industry": True, "description": "汽车与零部件研发、冲压、焊装、涂装、总装及检验", "modules": ["研发与零部件", "制造与装配", "质量追溯"]},
    {"id": "high_end_equipment", "name": "高端装备", "industry_group": "discrete", "primary_industry": True, "description": "工业装备、精密机械、机器人与装备研发制造", "modules": ["研发与设计", "加工与装调", "检测与运维"]},
    {"id": "electronics", "name": "电子制造", "industry_group": "discrete", "primary_industry": True, "description": "电子元器件、电路板、电子装联与整机制造", "modules": ["元器件与电路", "装联与生产", "测试与质量"]},
    {"id": "consumer_goods", "name": "消费品制造", "industry_group": "discrete", "primary_industry": True, "description": "日用消费品、家电与包装制造；以已有化妆品制造工艺图文库为典型案例", "modules": ["产品与包装", "工艺与生产", "检验与追溯"]},
    {"id": "materials", "name": "材料", "industry_group": "process", "primary_industry": True, "description": "冶炼、轧制、聚合、烧结等材料连续或批次生产工艺", "modules": ["配方与物性", "生产工艺流程", "过程控制与检验"]},
    {"id": "energy", "name": "能源", "industry_group": "process", "primary_industry": True, "description": "发电、炼化、制氢等能源生产与转换的工艺过程", "modules": ["生产与转换工艺", "PFD / P&ID", "过程控制与运行"]},
    {"id":"cosmetics_manufacturing","name":"化妆品制造工艺 AI 系统专属","description":"化妆品包材、玻璃瓶、涂装图文及文本训练参考；来源和审核状态分别保留","parent_id":"consumer_goods","exclusive_to_system":"化妆品制造工艺AI系统","local_only":True,"modules":["工艺图文参考","文本训练参考","来源与审核"]},
    {"id": "manufacturing", "name": "制造通用", "description": "跨行业 SOP、质量体系、工艺术语与生产案例", "modules": ["SOP 与工艺", "质量与缺陷", "设备与维护"]},
    {"id": "traditional", "name": "传统制造", "description": "包材、喷涂、机械加工等传统制造资料", "modules": ["包材与喷涂", "加工与成型", "现场案例"]},
    {"id": "equipment_electronics", "name": "装备与电子制造", "description": "装备说明、电子装联、装调维修及故障资料", "modules": ["装备与装调", "电子装联", "检验与维修"]},
    {"id": "electrical", "name": "电气", "description": "配电、驱动、工业控制及电气维护", "modules": ["配电与设备", "控制与驱动", "电气维护"]},
]


def validate_base(value):
    if value not in {item['id'] for item in KNOWLEDGE_BASES}:
        raise ValueError('Unknown knowledge base')
    return value


def base_policy(base_id):
    """Apply dedicated-space policy to every ingestion path, including uploads."""
    base=next((item for item in KNOWLEDGE_BASES if item['id']==base_id),{})
    if not base.get('local_only'):
        return {}
    return {'exclusive_to_system':base['exclusive_to_system'],
            'source_system':base['exclusive_to_system'],'allow_cloud_upload':False,
            'allow_public_redistribution':False,'allow_training':False}
