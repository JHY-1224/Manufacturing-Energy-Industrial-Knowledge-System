from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any


RAGAS_METRIC_KEYS = [
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
]


METRIC_DEFINITIONS: list[dict[str, Any]] = [
    {
        "key": "faithfulness",
        "title": "Faithfulness 忠实度",
        "evaluation_object": "答案可信度",
        "formula": "被上下文支持的声明数 / 答案中的总声明数",
        "definition": "评估生成答案是否被 retrieved_contexts 支持，用于判断答案是否存在幻觉。",
        "meaning": "把回答拆成事实声明，逐条判断是否能从检索上下文中得到支持。",
        "low_score_signals": [
            "回答包含检索上下文没有的故障原因",
            "编造设备参数、收益、电价、模型结果",
            "表格中不存在的数据被说成存在",
            "负荷预测问答中编造天气或节假日字段",
        ],
        "optimization_methods": [
            "加强 Prompt 证据约束",
            "启用无证据拒答",
            "强制引用 source_chunk_id",
            "开启 Context Compression",
            "表格问答恢复完整表格或摘要",
            "使用结构化输出模板",
        ],
    },
    {
        "key": "answer_relevancy",
        "title": "Answer Relevancy 答案相关性",
        "evaluation_object": "答案贴题度",
        "formula": "答案能否反推出原始问题的语义匹配度",
        "definition": "评估生成答案是否真正回答了用户问题。",
        "meaning": "不直接判断事实正确性，而是判断答案是否贴题、完整、没有偏题和无关扩展。",
        "low_score_signals": [
            "用户问变量定义，回答成故障诊断流程",
            "用户问 lag_96，回答成模型选择",
            "用户问 SOC/SOH 区别，回答成储能系统组成",
            "回答包含大量无关背景",
        ],
        "optimization_methods": [
            "增加 Query Rewrite",
            "增加 Intent Router",
            "Prompt 要求先给结论",
            "输出模板约束",
            "补充 Few-shot 示例",
            "限制无关背景扩展",
        ],
    },
    {
        "key": "context_precision",
        "title": "Context Precision 检索精确度",
        "evaluation_object": "检索排序",
        "formula": "相关 chunk 在 Top-K 中的排序质量",
        "definition": "评估检索到的 context 中，相关 chunk 是否排在前面。",
        "meaning": "分数高表示相关内容排序靠前；分数低表示相关 chunk 排名靠后或混入无关 chunk。",
        "low_score_signals": [
            "相关 chunk 没排到前面",
            "风电问题召回储能或负荷预测文档",
            "变量名、专业缩写匹配不准",
            "chunk 太大导致多主题混杂",
            "召回后没有 rerank",
        ],
        "optimization_methods": [
            "加强 Metadata Filter",
            "启用 Vector + BM25 混合检索",
            "使用 RRF 融合",
            "启用 Rerank",
            "优化业务对象切分",
            "优化能源术语 embedding",
        ],
    },
    {
        "key": "context_recall",
        "title": "Context Recall 检索召回率",
        "evaluation_object": "检索覆盖",
        "formula": "必要参考上下文被召回的完整度",
        "definition": "评估回答问题所需的关键上下文是否被完整召回。",
        "meaning": "需要 ground_truth 或 reference_contexts，用于判断检索系统是否找到所有必要证据。",
        "low_score_signals": [
            "必要证据没有被召回",
            "多跳问题只召回一部分链路",
            "表格被切碎，关键数据未进入 context",
            "父文档上下文未带回",
            "top_k 太小或 metadata filter 过窄",
        ],
        "optimization_methods": [
            "提高 top_k",
            "启用多查询检索",
            "启用 Query Decomposition",
            "启用 Parent Document Retriever",
            "使用父子文档索引",
            "恢复表格资产",
            "在需要时使用 Graph-enhanced Retrieval",
        ],
    },
]


FAILURE_TYPES: list[dict[str, str]] = [
    {
        "type": "retrieval_miss",
        "description": "必要参考 chunk 没有被召回。",
        "metric": "Context Recall",
    },
    {
        "type": "bad_ranking",
        "description": "相关 chunk 被召回了，但排序太靠后。",
        "metric": "Context Precision",
    },
    {
        "type": "noisy_context",
        "description": "Top-K 中混入大量无关 chunk。",
        "metric": "Context Precision / Faithfulness",
    },
    {
        "type": "hallucination",
        "description": "答案包含 retrieved_contexts 不支持的内容。",
        "metric": "Faithfulness",
    },
    {
        "type": "off_topic",
        "description": "答案偏题，没有正面回答用户问题。",
        "metric": "Answer Relevancy",
    },
    {
        "type": "incomplete_answer",
        "description": "答案只回答了一部分。",
        "metric": "Answer Relevancy / Context Recall",
    },
    {
        "type": "wrong_domain",
        "description": "检索到了错误知识域。",
        "metric": "Context Precision",
    },
    {
        "type": "bad_citation",
        "description": "引用来源错误或缺失。",
        "metric": "Faithfulness / source_citation",
    },
]


METRIC_GUIDE: list[dict[str, str]] = [
    {
        "metric": "Context Precision",
        "object": "检索排序",
        "low_score": "相关 chunk 排名靠后，无关 chunk 混入前列",
        "optimization": "Metadata Filter、混合检索、RRF、Rerank、优化 chunk 粒度、优化 embedding",
    },
    {
        "metric": "Context Recall",
        "object": "检索覆盖",
        "low_score": "必要证据没召回，多跳信息缺失",
        "optimization": "提高 top_k、多查询、Query Decomposition、父子文档索引、表格恢复、Graph-enhanced Retrieval",
    },
    {
        "metric": "Faithfulness",
        "object": "答案可信度",
        "low_score": "回答包含上下文不支持的内容",
        "optimization": "Prompt 约束、引用来源、无证据拒答、Context Compression、结构化输出",
    },
    {
        "metric": "Answer Relevancy",
        "object": "答案贴题度",
        "low_score": "回答偏题、啰嗦、没有正面回答",
        "optimization": "Query Rewrite、Intent Router、Few-shot、输出模板、拒绝无关扩展",
    },
]


ENERGY_SCENARIOS: list[dict[str, Any]] = [
    {
        "domain": "wind_oam",
        "name": "风电故障诊断",
        "metric_notes": {
            "Context Precision": "是否把 AccY 变量、塔架共振规则、故障案例排在前面。",
            "Context Recall": "是否同时召回变量解释、故障规则、案例证据和运维建议。",
            "Faithfulness": "是否只根据规则和案例回答，避免编造故障原因。",
            "Answer Relevancy": "是否直接回答可能故障、需查看变量和人工复核方式。",
        },
    },
    {
        "domain": "load_forecast",
        "name": "区域负荷预测",
        "metric_notes": {
            "Context Precision": "是否把 lag_96、rolling_mean_96、数据质量规则、模型方法排在前面。",
            "Context Recall": "是否召回数据字典、特征工程、预测模型、评估指标和业务应用。",
            "Faithfulness": "是否避免编造天气字段、节假日字段和模型训练结果。",
            "Answer Relevancy": "是否围绕特征、模型、误差和 EMS 应用回答。",
        },
    },
    {
        "domain": "storage_ems",
        "name": "储能 EMS",
        "metric_notes": {
            "Context Precision": "是否优先召回 EMS、BMS、PCS、SOC、SOH、削峰填谷相关 chunk。",
            "Context Recall": "是否覆盖负荷预测、SOC 约束、PCS 功率约束和充放电策略。",
            "Faithfulness": "是否避免编造储能收益、电价规则和设备参数。",
            "Answer Relevancy": "是否直接解释 EMS 场景，而不是泛泛讲能源管理。",
        },
    },
    {
        "domain": "calc_logic",
        "name": "工业计算逻辑",
        "metric_notes": {
            "Context Precision": "是否召回 WINDOW、STAT、FFT、AXIS 等正确计算规则。",
            "Context Recall": "是否召回变量规则、计算表达式、单位规则和纠错规则。",
            "Faithfulness": "是否避免生成知识库没有定义的计算表达式。",
            "Answer Relevancy": "是否直接回答绘图、统计、频谱或纠错逻辑。",
        },
    },
]


EVALUATION_DATASETS: list[dict[str, Any]] = [
    {
        "dataset_id": "wind_fault_qa",
        "name": "风电故障 QA",
        "domain": "wind_oam",
        "sample_count": 2,
        "samples": [
            {
                "eval_id": "wind_fault_eval_001",
                "question": "AccY 超限一般可能对应哪些风机振动问题？",
                "ground_truth": "AccY 超限可能与机舱横向振动、塔架一阶共振、偏航激励等有关，需要结合频域、工况和故障前后窗口判断。",
                "reference_context_ids": ["chunk_wind_accy_001", "chunk_wind_tower_002"],
                "domain": "wind_oam",
                "doc_type": "fault_rule",
                "difficulty": "medium",
                "tags": ["AccY", "tower_resonance", "fault_diagnosis"],
                "created_at": "2026-06-11",
                "updated_at": "2026-06-24",
            },
            {
                "eval_id": "wind_fault_eval_002",
                "question": "塔架一阶共振如何判断？",
                "ground_truth": "需要结合机舱加速度、0-5Hz FFT 主频峰值、转速区间、风速和功率波动进行人工复核。",
                "reference_context_ids": ["chunk_wind_tower_002"],
                "domain": "wind_oam",
                "doc_type": "fault_rule",
                "difficulty": "hard",
                "tags": ["FFT", "tower_resonance"],
                "created_at": "2026-06-11",
                "updated_at": "2026-06-24",
            },
        ],
    },
    {
        "dataset_id": "load_forecast_qa",
        "name": "负荷预测 QA",
        "domain": "load_forecast",
        "sample_count": 1,
        "samples": [
            {
                "eval_id": "load_forecast_eval_001",
                "question": "lag_96 在负荷预测中代表什么？",
                "ground_truth": "15 分钟级负荷序列中 lag_96 表示前一日同一时刻负荷，用于捕捉日周期。",
                "reference_context_ids": ["chunk_load_lag96_001"],
                "domain": "load_forecast",
                "doc_type": "feature_engineering",
                "difficulty": "easy",
                "tags": ["lag_96", "feature_engineering"],
                "created_at": "2026-06-11",
                "updated_at": "2026-06-24",
            },
        ],
    },
    {
        "dataset_id": "storage_ems_qa",
        "name": "储能 EMS QA",
        "domain": "storage_ems",
        "sample_count": 1,
        "samples": [
            {
                "eval_id": "storage_ems_eval_001",
                "question": "SOC 和 SOH 有什么区别？",
                "ground_truth": "SOC 描述当前剩余电量比例，SOH 描述电池健康状态，EMS 调度需要同时考虑二者。",
                "reference_context_ids": ["chunk_storage_soc_001"],
                "domain": "storage_ems",
                "doc_type": "data_dictionary",
                "difficulty": "easy",
                "tags": ["SOC", "SOH", "EMS"],
                "created_at": "2026-06-11",
                "updated_at": "2026-06-24",
            },
        ],
    },
    {
        "dataset_id": "calc_logic_qa",
        "name": "工业计算逻辑 QA",
        "domain": "calc_logic",
        "sample_count": 1,
        "samples": [
            {
                "eval_id": "calc_logic_eval_001",
                "question": "绘制故障前机舱加速度 0-5Hz 频谱应该生成什么计算逻辑？",
                "ground_truth": "应选择故障前窗口，取机舱加速度变量并执行 0-5Hz FFT 或能量谱计算，保留单位与窗口说明。",
                "reference_context_ids": ["chunk_calc_fft_001"],
                "domain": "calc_logic",
                "doc_type": "calculation_rule",
                "difficulty": "hard",
                "tags": ["FFT", "WINDOW", "AXIS"],
                "created_at": "2026-06-11",
                "updated_at": "2026-06-24",
            },
        ],
    },
    {
        "dataset_id": "mixed_energy_qa",
        "name": "混合能源 QA",
        "domain": "mixed_energy",
        "sample_count": 1,
        "samples": [
            {
                "eval_id": "mixed_energy_eval_001",
                "question": "负荷预测如何服务储能削峰填谷？",
                "ground_truth": "负荷预测为 EMS 提供未来负荷走势，结合 SOC、PCS 功率约束和电价策略生成充放电计划。",
                "reference_context_ids": ["chunk_load_lag96_001", "chunk_storage_soc_001"],
                "domain": "mixed_energy",
                "doc_type": "cross_domain_qa",
                "difficulty": "medium",
                "tags": ["load_forecast", "storage_ems", "peak_shaving"],
                "created_at": "2026-06-11",
                "updated_at": "2026-06-24",
            },
        ],
    },
]


EVALUATION_RUNS: list[dict[str, Any]] = [
    {
        "run_id": "ragas_run_20260624_001",
        "eval_dataset": "energy_rag_eval.jsonl",
        "embedding_model": "bge-large-zh",
        "vector_db": "chroma",
        "retriever_config": "hybrid + metadata + parent-child + rerank + compression",
        "retrieval_config": "hybrid+parent+rerank+compression",
        "reranker_model": "score-fallback",
        "top_k": 10,
        "score_threshold": 0.62,
        "started_at": "2026-06-24 20:00",
        "finished_at": "2026-06-24 20:08",
        "status": "completed",
        "sample_count": 11,
        "avg_faithfulness": 0.87,
        "avg_answer_relevancy": 0.84,
        "avg_context_precision": 0.81,
        "avg_context_recall": 0.78,
    },
    {
        "run_id": "ragas_run_20260623_001",
        "eval_dataset": "energy_rag_eval.jsonl",
        "embedding_model": "bge-large-zh",
        "vector_db": "chroma",
        "retriever_config": "hybrid + metadata",
        "retrieval_config": "hybrid+metadata",
        "reranker_model": "disabled",
        "top_k": 8,
        "score_threshold": 0.58,
        "started_at": "2026-06-23 20:00",
        "finished_at": "2026-06-23 20:05",
        "status": "completed",
        "sample_count": 11,
        "avg_faithfulness": 0.83,
        "avg_answer_relevancy": 0.80,
        "avg_context_precision": 0.74,
        "avg_context_recall": 0.72,
    },
]


SAMPLE_RESULTS: list[dict[str, Any]] = [
    {
        "run_id": "ragas_run_20260624_001",
        "eval_id": "wind_fault_eval_001",
        "question": "AccY 超限一般可能对应哪些风机振动问题？",
        "ground_truth": "AccY 超限可能与机舱横向振动、塔架一阶共振、偏航激励等有关，需要结合频域、工况和故障前后窗口判断。",
        "retrieved_contexts": [
            "AccY 是机舱 Y 向加速度，持续超限通常需要结合风速、转速、功率、主频峰值判断塔架共振或机舱横向振动风险。",
            "塔架一阶共振应使用 10 分钟振动窗口进行 0-5Hz 频谱分析，并结合转速区间、功率波动与主频峰值进行人工复核。",
        ],
        "retrieved_chunk_ids": ["chunk_wind_accy_001", "chunk_wind_tower_002"],
        "reference_context_ids": ["chunk_wind_accy_001", "chunk_wind_tower_002"],
        "generated_answer": "AccY 超限通常说明机舱横向振动增强，需结合塔架一阶共振规则、0-5Hz 频谱和工况窗口复核。",
        "answer": "AccY 超限通常说明机舱横向振动增强，需结合塔架一阶共振规则、0-5Hz 频谱和工况窗口复核。",
        "metrics": {
            "faithfulness": 0.92,
            "answer_relevancy": 0.88,
            "context_precision": 0.84,
            "context_recall": 0.90,
        },
        "faithfulness": 0.92,
        "answer_relevancy": 0.88,
        "context_precision": 0.84,
        "context_recall": 0.90,
        "failure_type": None,
        "optimization_suggestion": "当前样本表现正常，继续保持 hybrid + rerank + compression 配置。",
        "suggestion": "当前样本表现正常",
        "source_chunks": ["chunk_wind_accy_001", "chunk_wind_tower_002"],
        "trace": {"latency_ms": 1180, "retrieval_mode": "hybrid"},
    },
    {
        "run_id": "ragas_run_20260624_001",
        "eval_id": "calc_logic_eval_001",
        "question": "绘制故障前机舱加速度 0-5Hz 频谱应该生成什么计算逻辑？",
        "ground_truth": "应选择故障前窗口，取机舱加速度变量并执行 0-5Hz FFT 或能量谱计算，保留单位与窗口说明。",
        "retrieved_contexts": ["AccY 是机舱 Y 向加速度，可用于振动分析。"],
        "retrieved_chunk_ids": ["chunk_wind_accy_001"],
        "reference_context_ids": ["chunk_calc_fft_001"],
        "generated_answer": "选择 AccY 后绘制趋势即可。",
        "answer": "选择 AccY 后绘制趋势即可。",
        "metrics": {
            "faithfulness": 0.86,
            "answer_relevancy": 0.67,
            "context_precision": 0.48,
            "context_recall": 0.42,
        },
        "faithfulness": 0.86,
        "answer_relevancy": 0.67,
        "context_precision": 0.48,
        "context_recall": 0.42,
        "failure_type": "retrieval_miss",
        "optimization_suggestion": "必要计算规则未召回，建议提高 top_k、增加 Query Decomposition，并补强 calc_logic metadata filter。",
        "suggestion": "必要计算规则未召回",
        "source_chunks": ["chunk_wind_accy_001"],
        "trace": {"latency_ms": 1320, "retrieval_mode": "hybrid"},
    },
]


CONFIG_COMPARISONS: list[dict[str, Any]] = [
    {
        "config_name": "Vector + BGE",
        "embedding_model": "bge-large-zh",
        "retrieval_mode": "vector",
        "vector_top_k": 20,
        "bm25_top_k": 0,
        "rerank_enabled": False,
        "faithfulness": 0.79,
        "answer_relevancy": 0.77,
        "context_precision": 0.66,
        "context_recall": 0.69,
        "avg_latency_ms": 820,
    },
    {
        "config_name": "Hybrid + Metadata",
        "embedding_model": "bge-large-zh",
        "retrieval_mode": "hybrid",
        "vector_top_k": 20,
        "bm25_top_k": 20,
        "rerank_enabled": False,
        "faithfulness": 0.83,
        "answer_relevancy": 0.80,
        "context_precision": 0.74,
        "context_recall": 0.72,
        "avg_latency_ms": 980,
    },
    {
        "config_name": "Parent-Child + Rerank + Compression",
        "embedding_model": "bge-large-zh",
        "retrieval_mode": "parent_child",
        "vector_top_k": 20,
        "bm25_top_k": 20,
        "rerank_enabled": True,
        "faithfulness": 0.87,
        "answer_relevancy": 0.84,
        "context_precision": 0.81,
        "context_recall": 0.78,
        "avg_latency_ms": 1240,
    },
    {
        "config_name": "RAG-Fusion + Rerank",
        "embedding_model": "bge-large-zh",
        "retrieval_mode": "rag_fusion",
        "vector_top_k": 24,
        "bm25_top_k": 24,
        "rerank_enabled": True,
        "faithfulness": 0.88,
        "answer_relevancy": 0.85,
        "context_precision": 0.80,
        "context_recall": 0.82,
        "avg_latency_ms": 1580,
    },
]


METRIC_TRENDS: list[dict[str, Any]] = [
    {"date": "2026-06-20", "faithfulness": 0.78, "answer_relevancy": 0.76, "context_precision": 0.68, "context_recall": 0.66},
    {"date": "2026-06-21", "faithfulness": 0.80, "answer_relevancy": 0.78, "context_precision": 0.70, "context_recall": 0.68},
    {"date": "2026-06-22", "faithfulness": 0.82, "answer_relevancy": 0.79, "context_precision": 0.72, "context_recall": 0.70},
    {"date": "2026-06-23", "faithfulness": 0.83, "answer_relevancy": 0.80, "context_precision": 0.74, "context_recall": 0.72},
    {"date": "2026-06-24", "faithfulness": 0.87, "answer_relevancy": 0.84, "context_precision": 0.81, "context_recall": 0.78},
]


def clamp_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def _metric_title(key: str) -> str:
    for metric in METRIC_DEFINITIONS:
        if metric["key"] == key:
            return metric["title"]
    return key


def optional_score(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(1.0, score)), 4) if isfinite(score) else None


def build_metric_cards(runs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    run_items = runs or []
    latest = run_items[0] if run_items else {}
    previous = run_items[1] if len(run_items) > 1 else {}
    cards = []
    for metric in METRIC_DEFINITIONS:
        key = metric["key"]
        current = optional_score(latest.get(f"avg_{key}", latest.get(key)))
        last = optional_score(previous.get(f"avg_{key}", previous.get(key)))
        delta = round(current - last, 4) if current is not None and last is not None else None
        cards.append(
            {
                **metric,
                "current_avg": current,
                "previous_avg": last,
                "delta": delta,
                "trend": None if delta is None else "up" if delta > 0 else "down" if delta < 0 else "flat",
                "suggestion_entry": metric["optimization_methods"][0],
            }
        )
    return cards


def diagnose_scores(scores: dict[str, Any]) -> dict[str, Any]:
    normalized = {key: optional_score(scores.get(key)) for key in RAGAS_METRIC_KEYS}
    if any(value is None for value in normalized.values()):
        return {
            "primary_failure_type": None,
            "status": "insufficient_data",
            "root_cause": "核心指标尚未完整测量，不能自动诊断。",
            "suggestions": [],
            "triggered_rules": [],
            "combined_findings": [],
            "scores": normalized,
        }
    triggered: list[dict[str, Any]] = []

    if normalized["context_recall"] < 0.7:
        triggered.append(
            {
                "failure_type": "retrieval_miss",
                "metric": "context_recall",
                "root_cause": "必要证据召回不足，可能导致模型基于不完整上下文回答。",
                "suggestions": ["提高 top_k", "增加 Query Expansion", "启用 Parent Document Retriever", "检查 metadata filter 是否过窄"],
                "priority": 10,
            }
        )
    if normalized["context_precision"] < 0.7:
        triggered.append(
            {
                "failure_type": "bad_ranking",
                "metric": "context_precision",
                "root_cause": "相关 Chunk 排名靠后或 Top-K 混入无关内容。",
                "suggestions": ["启用 Rerank", "加强 Metadata Filter", "调整 BM25 权重", "优化 Chunk 粒度"],
                "priority": 8,
            }
        )
    if normalized["faithfulness"] < 0.8:
        triggered.append(
            {
                "failure_type": "hallucination",
                "metric": "faithfulness",
                "root_cause": "答案存在 retrieved_contexts 未支持内容。",
                "suggestions": ["加强 Prompt 证据约束", "强制引用来源", "开启无证据拒答", "压缩无关上下文"],
                "priority": 7,
            }
        )
    if normalized["answer_relevancy"] < 0.8:
        triggered.append(
            {
                "failure_type": "off_topic",
                "metric": "answer_relevancy",
                "root_cause": "答案与问题匹配度不足，可能偏题或没有正面回答。",
                "suggestions": ["增加 Query Rewrite", "启用 Intent Router", "补充 Few-shot 示例", "使用结构化输出模板"],
                "priority": 6,
            }
        )

    combined_findings: list[str] = []
    if normalized["context_recall"] < 0.7 and normalized["faithfulness"] < 0.8:
        combined_findings.append("Context Recall 低 + Faithfulness 低：可能是上下文缺失导致模型补全幻觉。")
    if normalized["context_precision"] < 0.7 and normalized["answer_relevancy"] < 0.8:
        combined_findings.append("Context Precision 低 + Answer Relevancy 低：可能是检索到无关文档导致回答偏题。")
    if normalized["context_precision"] < 0.7 <= normalized["context_recall"]:
        combined_findings.append("Context Precision 低 + Context Recall 高：召回够了但排序差，需要 Rerank。")
    if normalized["context_recall"] < 0.7 <= normalized["answer_relevancy"]:
        combined_findings.append("Context Recall 低 + Answer Relevancy 高：模型在努力回答，但证据不完整。")

    if not triggered:
        return {
            "primary_failure_type": None,
            "root_cause": "四个核心指标均处于可接受区间。",
            "suggestions": ["保持当前检索配置", "继续扩充覆盖低频问题的评测样本", "定期观察趋势和失败样本"],
            "triggered_rules": [],
            "combined_findings": combined_findings,
            "scores": normalized,
        }

    primary = sorted(triggered, key=lambda item: item["priority"], reverse=True)[0]
    suggestions = list(dict.fromkeys(suggestion for item in triggered for suggestion in item["suggestions"]))
    return {
        "primary_failure_type": primary["failure_type"],
        "root_cause": primary["root_cause"],
        "suggestions": suggestions,
        "triggered_rules": [
            {
                "failure_type": item["failure_type"],
                "metric": item["metric"],
                "root_cause": item["root_cause"],
                "suggestions": item["suggestions"],
            }
            for item in triggered
        ],
        "combined_findings": combined_findings,
        "scores": normalized,
    }


def diagnose_sample(metrics: dict[str, Any], reference_context_ids: list[str] | None = None, retrieved_chunk_ids: list[str] | None = None) -> dict[str, Any]:
    diagnosis = diagnose_scores(metrics)
    references = set(reference_context_ids or [])
    retrieved = set(retrieved_chunk_ids or [])
    missing = sorted(references - retrieved)
    matched = sorted(references & retrieved)

    if missing and metrics.get("context_recall", 1.0) < 0.75:
        diagnosis = {
            **diagnosis,
            "primary_failure_type": "retrieval_miss",
            "root_cause": f"必要参考 chunk 未召回：{', '.join(missing)}。",
            "suggestions": ["提高 top_k", "增加 Query Expansion", "启用 Parent Document Retriever", "检查 metadata filter 是否过窄"],
        }
    elif references and matched and metrics.get("context_precision", 1.0) < 0.7:
        diagnosis = {
            **diagnosis,
            "primary_failure_type": "bad_ranking",
            "root_cause": "参考 chunk 已召回但排序质量不足。",
            "suggestions": ["启用 Rerank", "调整 RRF/BM25 权重", "缩小同主题 chunk 粒度"],
        }

    return diagnosis


def build_evaluation_summary(
    extra_runs: list[dict[str, Any]] | None = None,
    *,
    reports: list[dict[str, Any]] | None = None,
    datasets: list[dict[str, Any]] | None = None,
    demo: bool = False,
) -> dict[str, Any]:
    runs = list(extra_runs or [])
    known = {item.get("run_id") for item in runs}
    if demo:
        runs.extend({**item, "data_source": "demo", "evaluation_mode": "demo"} for item in EVALUATION_RUNS if item["run_id"] not in known)
    samples = [item for report in (reports or []) for item in report.get("sample_results", [])]
    failures = [item for report in (reports or []) for item in report.get("failed_cases", [])]
    if demo:
        samples.extend({**item, "data_source": "demo", "evaluation_mode": "demo"} for item in SAMPLE_RESULTS)
        failures.extend({**item, "data_source": "demo", "evaluation_mode": "demo"} for item in SAMPLE_RESULTS if item.get("failure_type"))
    trend = [
        {"run_id": run.get("run_id"), "date": run.get("finished_at") or run.get("started_at"), "evaluation_mode": run.get("evaluation_mode", "unspecified"), **{key: optional_score(run.get(f"avg_{key}")) for key in RAGAS_METRIC_KEYS}}
        for run in reversed(runs)
    ]
    comparisons = [
        {"run_id": run.get("run_id"), "config_name": run.get("experiment") or run.get("retriever_config"), "embedding_model": run.get("embedding_model"), "retrieval_mode": run.get("retrieval_config"), "evaluation_mode": run.get("evaluation_mode", "unspecified"), "avg_latency_ms": run.get("metrics", {}).get("latency_total_ms"), **{key: optional_score(run.get(f"avg_{key}")) for key in RAGAS_METRIC_KEYS}}
        for run in runs
    ]
    metric_cards = build_metric_cards(runs)
    current_mode = runs[0].get("evaluation_mode", "unspecified") if runs else None
    if current_mode == "offline_proxy":
        proxy_formulas = {
            "faithfulness": "上下文与答案的不同词项交集 / 答案不同词项数",
            "answer_relevancy": "答案与问题的不同词项交集 / 问题不同词项数",
            "context_precision": "与问题有词项交集的片段数 / 返回片段数（不评价排序）",
            "context_recall": "上下文覆盖参考答案的不同词项比例；无参考答案时以问题词项代理",
        }
        metric_cards = [dict(card, evaluation_mode=current_mode, ragas_definition=card["definition"],
                             formula=proxy_formulas[card["key"]], definition="离线词项重合代理指标；不验证事实声明、语义或工业正确性。") for card in metric_cards]
    latest_scores = {card["key"]: card["current_avg"] for card in metric_cards}
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_source": "real_and_demo" if demo and extra_runs else "demo" if demo else "stored_reports",
        "evaluation_mode": current_mode,
        "evaluation_limitation": "offline_proxy 表示词项重合代理评分，不是 LLM/RAGAS 事实评判；历史报告未声明评分方式时保留 unspecified。",
        "metric_cards": metric_cards,
        "metric_definitions": metric_cards,
        "failure_types": FAILURE_TYPES,
        "metric_guide": METRIC_GUIDE,
        "energy_scenarios": ENERGY_SCENARIOS,
        "datasets": list(datasets or []) + ([{**item, "data_source": "demo"} for item in EVALUATION_DATASETS] if demo else []),
        "runs": runs,
        "sample_results": samples,
        "failures": failures,
        "trend": trend,
        "config_comparisons": comparisons,
        "automatic_diagnosis": diagnose_scores(latest_scores),
        "metrics": [{"name": card["key"], "value": card["current_avg"], "group": "RAGAS"} for card in metric_cards],
        "averages": {
            key: next(card["current_avg"] for card in metric_cards if card["key"] == key)
            for key in RAGAS_METRIC_KEYS
        },
    }
