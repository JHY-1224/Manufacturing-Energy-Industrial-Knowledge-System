from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

try:
    import uvicorn
    from fastapi import FastAPI, HTTPException, Query, Request
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
    from typing import Literal
except ModuleNotFoundError as exc:
    missing = exc.name or "fastapi"
    print(f"Missing Python package: {missing}")
    print("Run this first:")
    print("  python -m pip install -r backend/requirements.txt")
    raise SystemExit(1)


class ChatRequest(BaseModel):
    runtime_mode: Literal["offline", "configured"] = "offline"
    question: str = Field(min_length=1, max_length=16000)
    scenario: str = "wind_oam"


class ProcessDocumentRequest(BaseModel):
    chunk_mode: str = "parent_child"
    chunk_size: int = Field(default=700, ge=200, le=16000)
    chunk_overlap: int = Field(default=120, ge=0, le=8000)
    embedding_model: str = "bge-large-zh-v1.5"
    retrieval_mode: str = "hybrid_rerank"
    vector_top_k: int = Field(default=20, ge=1, le=100)
    final_top_k: int = Field(default=5, ge=1, le=100)
    enable_rerank: bool = True
    rerank_model: str = "bge-reranker-v2-m3"


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=16000)
    runtime_mode: Literal["offline", "configured"] = "offline"
    mode: Literal["hybrid_rerank", "hybrid", "bm25", "vector", "parent_child", "parent_child_hybrid", "summary", "rag_fusion"] = "hybrid_rerank"
    domain: str = "all"
    doc_type: str = "all"
    vector_top_k: int = Field(default=20, ge=1, le=100)
    final_top_k: int = Field(default=5, ge=1, le=100)
    enable_rerank: bool = True


UPLOAD_DIR = Path(__file__).resolve().parent / "uploaded_documents"


DOMAINS = [
    {
        "key": "wind_oam",
        "name": "风电智能运维",
        "summary": "风机变量字典、故障规则库、案例复盘、工程师反馈与运维建议。",
        "topics": ["AccX / AccY", "过速故障", "塔架共振", "变桨异常", "B 文件案例复盘"],
    },
    {
        "key": "load_forecast",
        "name": "区域负荷预测",
        "summary": "负荷数据字典、数据质量规则、特征工程、预测模型和评估指标。",
        "topics": ["15 分钟负荷", "lag_96", "rolling_mean_96", "MAE / RMSE / MAPE", "削峰填谷"],
    },
    {
        "key": "storage_ems",
        "name": "储能 EMS",
        "summary": "EMS / BMS / PCS、SOC / SOH、削峰填谷、需量管理与储能运行日报。",
        "topics": ["EMS / BMS / PCS", "SOC / SOH", "负荷预测", "需量管理", "AI 应用场景"],
    },
    {
        "key": "calc_logic",
        "name": "工业计算逻辑",
        "summary": "原始变量绘图、派生变量、时间窗口、统计值、FFT 和纠错规则。",
        "topics": ["WINDOW", "STAT", "FFT", "AXIS", "用户纠错"],
    },
    {
        "key": "report_template",
        "name": "报告模板",
        "summary": "风电诊断、区域负荷、负荷预测、储能 EMS 日报的结构化模板。",
        "topics": ["诊断报告", "负荷分析", "预测报告", "EMS 日报"],
    },
]

KNOWLEDGE_ENTRIES = [
    {
        "id": "ke-001",
        "title": "AccY 机舱 Y 向加速度变量定义",
        "domain": "wind_oam",
        "category": "风机变量字典",
        "sourceDoc": "wind_variable_dictionary.md",
        "chunkCount": 7,
        "tags": ["AccY", "变量解释", "振动"],
        "status": "vectorized",
        "updatedAt": "2026-06-12 09:30",
    },
    {
        "id": "ke-002",
        "title": "塔架一阶共振诊断规则",
        "domain": "wind_oam",
        "category": "风电故障规则库",
        "sourceDoc": "wind_fault_rules.pdf",
        "chunkCount": 12,
        "tags": ["tower_resonance", "FFT", "AccX", "AccY"],
        "status": "vectorized",
        "updatedAt": "2026-06-12 11:20",
    },
    {
        "id": "ke-003",
        "title": "15 分钟级区域负荷特征工程",
        "domain": "load_forecast",
        "category": "特征工程方法",
        "sourceDoc": "load_feature_engineering.md",
        "chunkCount": 23,
        "tags": ["lag_96", "rolling_mean_96", "负荷预测"],
        "status": "processing",
        "updatedAt": "2026-06-11 16:40",
    },
    {
        "id": "ke-004",
        "title": "EMS、BMS、PCS 协同关系说明",
        "domain": "storage_ems",
        "category": "EMS 基础",
        "sourceDoc": "storage_ems_basics.pdf",
        "chunkCount": 11,
        "tags": ["EMS", "BMS", "PCS"],
        "status": "vectorized",
        "updatedAt": "2026-06-10 14:05",
    },
    {
        "id": "ke-005",
        "title": "FFT 频谱窗口计算逻辑",
        "domain": "calc_logic",
        "category": "FFT / 能量谱规则",
        "sourceDoc": "industrial_calculation_rules.md",
        "chunkCount": 9,
        "tags": ["FREQWIN", "FFT", "WINDOW"],
        "status": "vectorized",
        "updatedAt": "2026-06-09 10:18",
    },
    {
        "id": "ke-006",
        "title": "风电故障诊断报告模板",
        "domain": "report_template",
        "category": "风电故障诊断报告模板",
        "sourceDoc": "report_templates.docx",
        "chunkCount": 8,
        "tags": ["报告模板", "诊断结论", "引用来源"],
        "status": "vectorized",
        "updatedAt": "2026-06-08 18:22",
    },
]

DOCUMENTS = [
    {
        "document_id": "doc_wind_rules_001",
        "title": "风电故障诊断规则库",
        "domain": "wind_oam",
        "doc_type": "fault_rule",
        "file_type": "PDF",
        "file_size": "12.6 MB",
        "version": "v1.4",
        "status": "published",
        "chunk_count": 86,
        "embedding_status": "embedded",
        "vector_index_status": "indexed",
        "created_at": "2026-06-08 09:10",
        "updated_at": "2026-06-12 09:30",
        "progress": 100,
    },
    {
        "document_id": "doc_load_feature_001",
        "title": "区域负荷预测建模规范",
        "domain": "load_forecast",
        "doc_type": "feature_engineering",
        "file_type": "Markdown",
        "file_size": "860 KB",
        "version": "v0.9",
        "status": "indexed",
        "chunk_count": 54,
        "embedding_status": "embedded",
        "vector_index_status": "indexed",
        "created_at": "2026-06-09 11:00",
        "updated_at": "2026-06-11 16:40",
        "progress": 92,
    },
    {
        "document_id": "doc_storage_ems_001",
        "title": "储能 EMS 运行知识库",
        "domain": "storage_ems",
        "doc_type": "data_dictionary",
        "file_type": "PDF",
        "file_size": "8.4 MB",
        "version": "v1.1",
        "status": "published",
        "chunk_count": 61,
        "embedding_status": "embedded",
        "vector_index_status": "indexed",
        "created_at": "2026-06-10 08:30",
        "updated_at": "2026-06-10 14:05",
        "progress": 100,
    },
    {
        "document_id": "doc_calc_logic_001",
        "title": "工业计算逻辑说明",
        "domain": "calc_logic",
        "doc_type": "calculation_rule",
        "file_type": "Markdown",
        "file_size": "420 KB",
        "version": "v1.0",
        "status": "metadata_ready",
        "chunk_count": 37,
        "embedding_status": "pending",
        "vector_index_status": "pending",
        "created_at": "2026-06-11 10:20",
        "updated_at": "2026-06-12 10:18",
        "progress": 58,
    },
]

CHUNKS = [
    {
        "chunk_id": "chunk_wind_accy_001",
        "document_id": "doc_wind_rules_001",
        "chunk_title": "AccY 超限与塔架振动风险",
        "chunk_content": "AccY 是机舱 Y 向加速度，持续超限通常需要结合风速、转速、功率、主频峰值判断塔架共振或机舱横向振动风险。",
        "domain": "wind_oam",
        "doc_type": "variable_definition",
        "device_type": "wind_turbine",
        "scenario": "fault_diagnosis",
        "fault_type": "tower_resonance",
        "variables": ["AccY", "WindSpeed", "GeneratorSpeed"],
        "token_count": 326,
        "source_file": "wind_variable_dictionary.md",
        "version": "v1.4",
        "embedding_status": "embedded",
        "vector_index_status": "indexed",
        "score": 0.91,
        "rerank_score": 0.94,
        "created_at": "2026-06-08 09:40",
        "updated_at": "2026-06-12 09:30",
    },
    {
        "chunk_id": "chunk_wind_tower_002",
        "document_id": "doc_wind_rules_001",
        "chunk_title": "塔架一阶共振 FFT 诊断分支",
        "chunk_content": "塔架一阶共振应使用 10 分钟振动窗口进行 0-5Hz 频谱分析，并结合转速区间、功率波动与主频峰值进行人工复核。",
        "domain": "wind_oam",
        "doc_type": "fault_rule",
        "device_type": "wind_turbine",
        "scenario": "fault_diagnosis",
        "fault_type": "tower_resonance",
        "variables": ["AccX", "AccY", "GridPower"],
        "token_count": 288,
        "source_file": "wind_fault_rules.pdf",
        "version": "v1.4",
        "embedding_status": "embedded",
        "vector_index_status": "indexed",
        "score": 0.88,
        "rerank_score": 0.9,
        "created_at": "2026-06-08 10:10",
        "updated_at": "2026-06-12 11:20",
    },
    {
        "chunk_id": "chunk_load_lag96_001",
        "document_id": "doc_load_feature_001",
        "chunk_title": "lag_96 与日周期负荷特征",
        "chunk_content": "15 分钟级负荷序列中 lag_96 表示前一日同一时刻负荷，是区域负荷预测中捕捉日周期的重要特征。",
        "domain": "load_forecast",
        "doc_type": "feature_engineering",
        "device_type": "regional_load",
        "scenario": "load_forecasting",
        "fault_type": "-",
        "variables": ["LoadPower", "lag_96", "rolling_mean_96"],
        "token_count": 402,
        "source_file": "load_feature_engineering.md",
        "version": "v0.9",
        "embedding_status": "embedded",
        "vector_index_status": "indexed",
        "score": 0.84,
        "rerank_score": 0.82,
        "created_at": "2026-06-09 11:30",
        "updated_at": "2026-06-11 16:40",
    },
    {
        "chunk_id": "chunk_storage_soc_001",
        "document_id": "doc_storage_ems_001",
        "chunk_title": "SOC 与 SOH 的区别",
        "chunk_content": "SOC 描述当前剩余电量比例，SOH 描述电池健康状态。EMS 调度需要同时考虑 SOC 安全窗口与 SOH 衰减风险。",
        "domain": "storage_ems",
        "doc_type": "data_dictionary",
        "device_type": "storage_system",
        "scenario": "storage_ems",
        "fault_type": "-",
        "variables": ["SOC", "SOH"],
        "token_count": 356,
        "source_file": "storage_ems_basics.pdf",
        "version": "v1.1",
        "embedding_status": "embedded",
        "vector_index_status": "indexed",
        "score": 0.86,
        "rerank_score": 0.87,
        "created_at": "2026-06-10 09:12",
        "updated_at": "2026-06-10 14:05",
    },
]

METADATA_TAGS = [
    {"name": "wind_oam", "type": "domain", "doc_count": 8, "chunk_count": 183, "updated_at": "2026-06-12"},
    {"name": "load_forecast", "type": "domain", "doc_count": 5, "chunk_count": 96, "updated_at": "2026-06-11"},
    {"name": "storage_ems", "type": "domain", "doc_count": 6, "chunk_count": 88, "updated_at": "2026-06-10"},
    {"name": "calc_logic", "type": "domain", "doc_count": 4, "chunk_count": 51, "updated_at": "2026-06-12"},
    {"name": "report_template", "type": "domain", "doc_count": 4, "chunk_count": 24, "updated_at": "2026-06-08"},
    {"name": "fault_rule", "type": "doc_type", "doc_count": 3, "chunk_count": 72, "updated_at": "2026-06-12"},
    {"name": "variable_definition", "type": "doc_type", "doc_count": 2, "chunk_count": 44, "updated_at": "2026-06-12"},
    {"name": "tower_resonance", "type": "fault_type", "doc_count": 2, "chunk_count": 19, "updated_at": "2026-06-12"},
    {"name": "AccY", "type": "variables", "doc_count": 3, "chunk_count": 21, "updated_at": "2026-06-12"},
    {"name": "SOC", "type": "variables", "doc_count": 2, "chunk_count": 16, "updated_at": "2026-06-10"},
]

VECTOR_CONFIG = {
    "embedding": {"model": "bge-large-zh-v1.5", "mode": "local", "dimension": 1024, "batchSize": 32, "status": "ready"},
    "vectorDb": {"engine": "Milvus / Chroma / FAISS / Qdrant", "indexName": "energy_oam_chunks", "indexedChunks": 386, "failedChunks": 3, "lastIndexTime": "2026-06-12 19:20"},
    "bm25": {"enabled": True, "backend": "local bm25", "indexedDocs": 27, "indexedChunks": 386},
    "hybrid": {"vectorTopK": 20, "bm25TopK": 20, "fusionMethod": "RRF", "finalTopK": 8},
    "rerank": {"model": "bge-reranker-v2-m3", "enabled": True, "topN": 8, "scoreThreshold": 0.62},
    "jobs": [
        {"job_id": "job-001", "document_id": "doc_wind_rules_001", "step": "publish", "progress": 100, "status": "completed", "logs": "published to retrieval API", "started_at": "2026-06-12 18:10", "finished_at": "2026-06-12 18:18"},
        {"job_id": "job-002", "document_id": "doc_load_feature_001", "step": "index", "progress": 92, "status": "running", "logs": "building hybrid index", "started_at": "2026-06-12 19:10", "finished_at": "-"},
        {"job_id": "job-003", "document_id": "doc_calc_logic_001", "step": "embed", "progress": 58, "status": "queued", "logs": "waiting for embedding worker", "started_at": "-", "finished_at": "-"},
    ],
}

EVALUATION = {
    "datasets": [
        {"eval_id": "eval-001", "question": "AccY 超限一般可能对应哪些风机振动问题？", "ground_truth": "塔架共振、机舱横向振动、传感器异常等，需要结合 FFT 和工况复核。", "expected_chunk_ids": ["chunk_wind_accy_001", "chunk_wind_tower_002"], "domain": "wind_oam", "doc_type": "fault_rule", "difficulty": "medium", "tags": ["AccY", "tower_resonance"], "created_at": "2026-06-11"},
        {"eval_id": "eval-002", "question": "lag_96 在负荷预测中代表什么？", "ground_truth": "前一日同一时刻负荷，用于捕捉日周期。", "expected_chunk_ids": ["chunk_load_lag96_001"], "domain": "load_forecast", "doc_type": "feature_engineering", "difficulty": "easy", "tags": ["lag_96"], "created_at": "2026-06-11"},
    ],
    "runs": [
        {"run_id": "run-20260612-01", "eval_dataset": "energy_stage1_eval", "retrieval_config": "hybrid+rerank", "embedding_model": "bge-large-zh-v1.5", "vector_db": "Milvus", "reranker_model": "bge-reranker-v2-m3", "top_k": 5, "status": "completed", "started_at": "2026-06-12 20:00", "finished_at": "2026-06-12 20:08"},
    ],
    "metrics": [
        {"name": "faithfulness", "value": 0.87, "group": "RAGAS"},
        {"name": "answer_relevancy", "value": 0.84, "group": "RAGAS"},
        {"name": "context_precision", "value": 0.81, "group": "RAGAS"},
        {"name": "context_recall", "value": 0.78, "group": "RAGAS"},
        {"name": "hit@k", "value": 0.9, "group": "工业指标"},
        {"name": "mrr", "value": 0.73, "group": "工业指标"},
        {"name": "source_citation_rate", "value": 0.96, "group": "工业指标"},
        {"name": "latency_ms", "value": 1240, "group": "工程指标"},
    ],
    "failures": [
        {"question": "塔架一阶共振如何判断？", "ground_truth": "需要 FFT 主频、转速、风速和功率综合判断。", "actual_answer": "只提到加速度超限。", "expected_chunk_ids": ["chunk_wind_tower_002"], "retrieved_chunk_ids": ["chunk_wind_accy_001"], "failed_type": "incomplete_answer", "metric_scores": "context_recall=0.42", "improvement_suggestion": "提高塔架共振规则 Chunk 的 BM25 权重并加入 fault_type 过滤。"},
    ],
}

RUNTIME = {
    "layers": ["Control Plane", "RAG Runtime", "Evaluation", "API Layer"],
    "mapping": [
        ["Document", "Document"],
        ["Chunk", "Node"],
        ["Metadata", "Node metadata"],
        ["Vector Index", "VectorStoreIndex"],
        ["BM25 检索", "BM25Retriever"],
        ["混合检索", "QueryFusionRetriever / 自定义 RRF"],
        ["Rerank", "Node Postprocessor / Reranker"],
        ["问答引擎", "QueryEngine"],
        ["引用来源", "Source Nodes"],
        ["评估集", "RAGAS dataset / 自定义 eval dataset"],
    ],
    "pipeline": ["Document", "Loader / Parser", "EnergyNodeParser", "Metadata Extractor", "Embedding", "VectorStoreIndex", "Retriever", "BM25 Retriever", "Hybrid Retriever", "RRF Fusion", "Reranker", "Query Engine", "Response Synthesizer", "Citation Builder"],
    "retrievalFlow": ["query", "intent/domain router", "metadata filter", "vector top_k=20", "bm25 top_k=20", "RRF top_k=15", "reranker top_k=5", "context builder", "LLM response"],
}

REPORTS = [
    {"id": "rp-001", "title": "WTG-A05 变桨系统异常诊断报告", "type": "风电故障诊断报告", "scenario": "风电故障复盘", "inputData": "SCADA 摘要 + 振动特征", "sources": ["chunk_wind_accy_001", "chunk_wind_tower_002"], "status": "generated", "generatedAt": "2026-06-12 17:30", "summary": "疑似编码器反馈异常并伴随振动风险，建议现场复核线缆屏蔽、接插件与驱动器日志。"},
    {"id": "rp-002", "title": "华东区域 15 分钟负荷分析报告", "type": "区域负荷分析报告", "scenario": "负荷预测特征解释", "inputData": "15 分钟负荷序列", "sources": ["chunk_load_lag96_001"], "status": "generated", "generatedAt": "2026-06-11 20:10", "summary": "午峰负荷受高温影响抬升，商业区负荷弹性高于居民区。"},
    {"id": "rp-003", "title": "储能 EMS 日运行简报", "type": "储能 EMS 运行日报", "scenario": "削峰填谷运行复盘", "inputData": "SOC / SOH / PCS 功率", "sources": ["chunk_storage_soc_001"], "status": "generating", "generatedAt": "2026-06-14 08:00", "summary": "削峰填谷策略执行稳定，SOC 保持在安全窗口内。"},
]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def ensure_upload_dir() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def safe_filename(name: str) -> str:
    keep = []
    for char in name.strip():
        if char.isalnum() or char in {".", "-", "_", " ", "(", ")", "[", "]"}:
            keep.append(char)
        else:
            keep.append("_")
    cleaned = "".join(keep).strip(" .")
    return cleaned or f"document-{uuid4().hex[:8]}.bin"


def format_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def infer_file_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    return {
        "pdf": "PDF",
        "doc": "Word",
        "docx": "Word",
        "md": "Markdown",
        "markdown": "Markdown",
        "csv": "CSV",
        "xls": "Excel",
        "xlsx": "Excel",
        "json": "JSON",
        "txt": "Text",
    }.get(suffix, suffix.upper() or "Binary")


def preview_text_from_bytes(content: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".markdown", ".csv", ".json", ".py", ".js", ".ts", ".vue", ".html", ".css"}:
        for encoding in ("utf-8", "gb18030", "latin-1"):
            try:
                return content.decode(encoding)[:12000]
            except UnicodeDecodeError:
                continue
    return (
        f"{filename} 已保存到本地上传目录。当前 Demo 对二进制文档先生成占位解析文本，"
        "后续可接入 unstructured / pymupdf / python-docx / pandas 做真实解析。"
    )


def split_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    text = " ".join(text.replace("\r", "\n").split())
    if not text:
        return []
    chunk_size = max(200, min(chunk_size, 1800))
    chunk_overlap = max(0, min(chunk_overlap, chunk_size // 2))
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - chunk_overlap
    return chunks


def find_document(document_id: str) -> dict:
    for document in DOCUMENTS:
        if document["document_id"] == document_id:
            return document
    raise HTTPException(status_code=404, detail="Document not found")


def build_chunks_for_document(document: dict, config: ProcessDocumentRequest) -> list[dict]:
    parsed_text = document.get("parsed_text") or document.get("title", "")
    parts = split_text(parsed_text, config.chunk_size, config.chunk_overlap) or [document.get("title", "")]
    generated: list[dict] = []
    for index, part in enumerate(parts, start=1):
        node_id = f"node_{document['document_id']}_{index:03d}"
        generated.append(
            {
                "chunk_id": node_id,
                "document_id": document["document_id"],
                "chunk_title": f"{document['title']} · Node {index}",
                "chunk_content": part,
                "domain": document["domain"],
                "doc_type": document["doc_type"],
                "device_type": document.get("device_type", "-"),
                "scenario": document.get("scenario", "-"),
                "fault_type": document.get("fault_type", "-"),
                "variables": document.get("variables", []),
                "token_count": max(1, len(part) // 2),
                "source_file": document.get("stored_filename", document["title"]),
                "version": document["version"],
                "embedding_status": "embedded",
                "vector_index_status": "indexed",
                "score": 0.78,
                "rerank_score": 0.82 if config.enable_rerank else 0.78,
                "created_at": now_text(),
                "updated_at": now_text(),
                "llama_index": {
                    "document_id": document["document_id"],
                    "node_id": node_id,
                    "node_type": "TextNode",
                    "parser": "EnergyNodeParser",
                    "chunk_mode": config.chunk_mode,
                    "embedding_model": config.embedding_model,
                    "retriever": config.retrieval_mode,
                    "reranker": config.rerank_model if config.enable_rerank else "disabled",
                },
            }
        )
    return generated


def score_chunk(query: str, chunk: dict, mode: str, enable_rerank: bool) -> dict:
    haystack = " ".join(
        [
            chunk.get("chunk_title", ""),
            chunk.get("chunk_content", ""),
            chunk.get("domain", ""),
            chunk.get("doc_type", ""),
            " ".join(chunk.get("variables", [])),
        ]
    ).lower()
    terms = [term.lower() for term in query.replace("？", " ").replace("?", " ").split() if term.strip()]
    hits = sum(1 for term in terms if term in haystack)
    base_score = float(chunk.get("score", 0.72))
    score = min(0.99, base_score + hits * 0.04)
    if mode == "bm25":
        score = min(0.99, 0.68 + hits * 0.08)
    elif mode == "vector":
        score = min(0.99, base_score + hits * 0.025)
    rerank_score = min(0.99, score + 0.03) if enable_rerank and "rerank" in mode else score
    return {**chunk, "score": round(score, 3), "rerank_score": round(rerank_score, 3)}


def run_retrieval(payload: RetrievalRequest) -> dict:
    from app.core.models import QueryRequest, QueryOptions
    from app.core.engine import DOMAIN_NAMES
    filters = {}
    if payload.domain != "all":
        filters["domain"] = DOMAIN_NAMES.get(payload.domain, payload.domain)
    if payload.doc_type != "all":
        filters["doc_type"] = payload.doc_type
    mode = "hybrid" if payload.mode == "hybrid_rerank" else payload.mode
    trace = knowledge_service().engine.query(QueryRequest(question=payload.query, options=QueryOptions(
        runtime_mode=payload.runtime_mode, retrieval_mode=mode, top_k=payload.vector_top_k, final_top_k=payload.final_top_k,
        rerank=payload.enable_rerank, metadata_filter=filters, metadata_router=False, query_transformation=False)))
    return {"query": payload.query, "mode": payload.mode, "trace_id": trace.trace_id,
        "items": [{"chunk_id": hit.chunk_id, "document_id": hit.doc_id, "chunk_content": hit.content,
                   "source_file": hit.metadata.get("source", ""), "domain": hit.metadata.get("domain", ""),
                   "doc_type": hit.metadata.get("doc_type", ""), "variables": hit.metadata.get("variables", []),
                   "score": hit.score, "rerank_score": hit.rerank_score, "rank": hit.final_rank,
                   "citation_id": hit.chunk_id, "metadata": hit.metadata} for hit in trace.reranked_docs],
        "citationChain": ["QueryTrace", "Chunk", "Document", "Revision"], "runtime": trace.runtime}


def knowledge_service():
    from app.api.dependencies import service, bootstrap_legacy_chunks
    bootstrap_legacy_chunks(CHUNKS)
    return service


def build_state() -> dict:
    return knowledge_service().state(DOMAINS, RUNTIME)


app = FastAPI(
    title="制造能源工业知识库系统 API",
    description="面向制造业，兼顾能源与电气领域的知识库管理与检索 API；当前保留能源示例及离线回退实现。",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8080", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"name": "制造能源工业知识库系统 API", "status": "ok", "docs": "http://127.0.0.1:8000/docs"}


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.now().isoformat(timespec="seconds")}


@app.get("/api/state")
def state():
    return deepcopy(build_state())


@app.get("/api/overview")
def overview():
    return deepcopy(build_state()["overview"])


@app.get("/api/knowledge")
def knowledge(domain: str | None = None, status: str | None = None, q: str = Query(default="")):
    q_lower = q.lower().strip()
    data = build_state()["knowledge"]
    if domain:
        data = [item for item in data if item["domain"] == domain]
    if status:
        data = [item for item in data if item["status"] == status]
    if q_lower:
        data = [item for item in data if q_lower in item["title"].lower() or any(q_lower in tag.lower() for tag in item["tags"])]
    return {"items": data}


@app.get("/api/documents")
def documents(page:int|None=None,page_size:int=Query(default=50,ge=1,le=100)):
    service=knowledge_service()
    if page is None:return {"items":service.documents()}
    from app.storage.workbench_read import WorkbenchReadStore
    if page<1:raise HTTPException(422,'Invalid page')
    result=WorkbenchReadStore(service.store).documents(page,page_size)
    result['items']=[service.document_view(d) for d in result['items']]
    return result


@app.post("/api/documents/upload")
async def upload_document(
    request: Request,
    filename: str = Query(...),
    domain: str = Query(default="wind_oam"),
    doc_type: str = Query(default="general"),
):
    parts, size = [], 0
    async for part in request.stream():
        size += len(part)
        if size > 64 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Document exceeds 64 MiB")
        parts.append(part)
    try:
        document = knowledge_service().upload(filename, b"".join(parts), domain, doc_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"document": document, "state": build_state()}


@app.post("/api/documents/{document_id}/process")
def process_document(document_id: str, payload: ProcessDocumentRequest):
    from app.storage.knowledge_store import KnowledgeConflictError
    try:
        result = knowledge_service().process(document_id, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KnowledgeConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, ModuleNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**result, "state": build_state()}


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: str):
    try:
        result = knowledge_service().delete(document_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {**result, "state": build_state()}


@app.get("/api/chunks")
def chunks(page:int|None=None,page_size:int=Query(default=50,ge=1,le=100)):
    service=knowledge_service()
    if page is None:return {"items":service.chunks()}
    from app.storage.workbench_read import WorkbenchReadStore
    from app.core.models import RagChunk
    if page<1:raise HTTPException(422,'Invalid page')
    result=WorkbenchReadStore(service.store).chunks(page,page_size)
    result['items']=[service.chunk_view(RagChunk.model_validate(c)) for c in result['items']]
    return result


@app.get("/api/metadata")
def metadata():
    return {"items": knowledge_service().metadata()}


@app.get("/api/vector-index")
def vector_index():
    return knowledge_service().vector_config()


@app.get("/api/evaluations")
def evaluations():
    from app.api.routes_eval import get_metrics_summary
    return get_metrics_summary()


@app.get("/api/retrieval")
def retrieval(query: str = Query(default="AccY 超限一般可能对应哪些风机振动问题？", min_length=1, max_length=16000), mode: Literal["hybrid_rerank", "hybrid", "bm25", "vector", "parent_child", "parent_child_hybrid", "summary", "rag_fusion"] = "hybrid_rerank"):
    return run_retrieval(RetrievalRequest(query=query, mode=mode))


@app.post("/api/retrieval/search")
def retrieval_search(payload: RetrievalRequest):
    return run_retrieval(payload)


@app.post("/api/context/build")
def build_context(payload: RetrievalRequest):
    result = run_retrieval(payload)
    context_text = "\n\n".join(
        f"【片段 {item['rank']}】{item['chunk_content']}\nsource={item['source_file']} chunk_id={item['chunk_id']}"
        for item in result["items"]
    )
    return {
        "query": payload.query,
        "context_text": context_text,
        "sources": result["items"],
        "trace_id": result["trace_id"],
        "runtime": result["runtime"],
    }


@app.post("/api/chat")
def chat(payload: ChatRequest):
    from app.core.models import QueryRequest, QueryOptions
    from app.core.engine import DOMAIN_NAMES
    trace = knowledge_service().engine.query(QueryRequest(question=payload.question,
        options=QueryOptions(runtime_mode=payload.runtime_mode, metadata_filter={"domain": DOMAIN_NAMES.get(payload.scenario, payload.scenario)})))
    return {"answer": trace.answer, "trace_id": trace.trace_id, "runtime": trace.runtime,
            "sources": [{"chunk_id": hit.chunk_id, "source_file": hit.metadata.get("source"),
                         "domain": hit.metadata.get("domain"), "doc_type": hit.metadata.get("doc_type"),
                         "score": hit.score, "rerank_score": hit.rerank_score, "revision_id": hit.metadata.get("revision_id")} for hit in trace.reranked_docs]}


@app.get("/api/reports")
def reports():
    from app.api.routes_eval import list_evaluation_runs
    return list_evaluation_runs()


if __name__ == "__main__":
    import sys

    project_root = str(Path(__file__).resolve().parents[1])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from app.main import app as modular_app

    uvicorn.run(modular_app, host="127.0.0.1", port=8000)
