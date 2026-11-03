export const demoState = {
  domains: [
    { key: 'wind_oam', name: '风电智能运维', summary: '风机变量字典、故障规则库、案例复盘、工程师反馈与运维建议。', topics: ['AccX / AccY', '过速故障', '塔架共振', '偏航与变桨异常', 'B 文件案例复盘'] },
    { key: 'load_forecast', name: '区域负荷预测', summary: '负荷数据字典、数据质量规则、特征工程、预测模型和评估指标。', topics: ['15 分钟负荷', 'lag_96', 'rolling_mean_96', 'MAE / RMSE / MAPE', '削峰填谷'] },
    { key: 'storage_ems', name: '储能 EMS', summary: 'EMS / BMS / PCS、SOC / SOH、削峰填谷、需量管理与储能运行日报。', topics: ['EMS / BMS / PCS', 'SOC / SOH', '负荷预测', '需量管理', 'AI 应用场景'] },
    { key: 'calc_logic', name: '工业计算逻辑', summary: '原始变量绘图、派生变量、时间窗口、统计值、FFT、同轴 / 双轴与纠错规则。', topics: ['WINDOW', 'STAT', 'FFT', 'AXIS', '用户纠错'] },
    { key: 'report_template', name: '报告模板', summary: '风电诊断、区域负荷、负荷预测、储能 EMS 日报的结构化模板。', topics: ['诊断报告', '负荷分析', '预测报告', 'EMS 日报'] },
  ],
  overview: {
    kpis: [
      { label: '知识条目', value: 25, delta: '+3 本周新增' },
      { label: '入库文档', value: 27, delta: '+2 本周新增' },
      { label: 'Chunk 数量', value: 442, delta: '+28 本周新增' },
      { label: 'Metadata 标签', value: 44, delta: '体系完整' },
      { label: '已索引 Chunk', value: 386, delta: '87.3% 已索引' },
      { label: '评估样本', value: 125, delta: '+5 本周新增' },
      { label: 'Context Precision', value: 0.81, delta: '+0.03 vs 上周' },
      { label: 'Faithfulness', value: 0.87, delta: '+0.02 vs 上周' },
    ],
    pipelineStatus: [
      ['Document Parse', 'Active'],
      ['Chunking', 'Active'],
      ['Embedding', 'Configurable'],
      ['Vector DB', 'Milvus / Chroma / FAISS / Qdrant'],
      ['BM25', 'Enabled'],
      ['RRF Fusion', 'Enabled'],
      ['Rerank', 'Enabled'],
      ['RAGAS Eval', 'Enabled'],
    ],
    recentDocumentJobs: [
      { job_id: 'job-001', document_id: 'doc_wind_rules_001', step: 'publish', progress: 100, status: 'completed', logs: 'published to retrieval API', started_at: '2026-06-12 18:10', finished_at: '2026-06-12 18:18' },
      { job_id: 'job-002', document_id: 'doc_load_feature_001', step: 'index', progress: 92, status: 'running', logs: 'building hybrid index', started_at: '2026-06-12 19:10', finished_at: '-' },
      { job_id: 'job-003', document_id: 'doc_calc_logic_001', step: 'embed', progress: 58, status: 'queued', logs: 'waiting for embedding worker', started_at: '-', finished_at: '-' },
    ],
    recentTests: [
      { question: 'AccY 超限一般可能对应哪些风机振动问题？', score: 0.91, time: '2026-06-12 15:20' },
      { question: 'lag_96 在负荷预测中代表什么？', score: 0.86, time: '2026-06-11 19:00' },
      { question: 'SOC 和 SOH 有什么区别？', score: 0.88, time: '2026-06-10 10:45' },
    ],
  },
  knowledge: [
    { id: 'ke-001', title: 'AccY 机舱 Y 向加速度变量定义', domain: 'wind_oam', category: '风机变量字典', sourceDoc: 'wind_variable_dictionary.md', chunkCount: 7, tags: ['AccY', '变量解释', '振动'], status: 'vectorized', updatedAt: '2026-06-12 09:30' },
    { id: 'ke-002', title: '塔架一阶共振诊断规则', domain: 'wind_oam', category: '风电故障规则库', sourceDoc: 'wind_fault_rules.pdf', chunkCount: 12, tags: ['tower_resonance', 'FFT', 'AccX', 'AccY'], status: 'vectorized', updatedAt: '2026-06-12 11:20' },
    { id: 'ke-003', title: '15 分钟级区域负荷特征工程', domain: 'load_forecast', category: '特征工程方法', sourceDoc: 'load_feature_engineering.md', chunkCount: 23, tags: ['lag_96', 'rolling_mean_96', '负荷预测'], status: 'processing', updatedAt: '2026-06-11 16:40' },
    { id: 'ke-004', title: 'EMS、BMS、PCS 协同关系说明', domain: 'storage_ems', category: 'EMS 基础', sourceDoc: 'storage_ems_basics.pdf', chunkCount: 11, tags: ['EMS', 'BMS', 'PCS'], status: 'vectorized', updatedAt: '2026-06-10 14:05' },
    { id: 'ke-005', title: 'FFT 频谱窗口计算逻辑', domain: 'calc_logic', category: 'FFT / 能量谱规则', sourceDoc: 'industrial_calculation_rules.md', chunkCount: 9, tags: ['FREQWIN', 'FFT', 'WINDOW'], status: 'vectorized', updatedAt: '2026-06-09 10:18' },
    { id: 'ke-006', title: '风电故障诊断报告模板', domain: 'report_template', category: '风电故障诊断报告模板', sourceDoc: 'report_templates.docx', chunkCount: 8, tags: ['报告模板', '诊断结论', '引用来源'], status: 'vectorized', updatedAt: '2026-06-08 18:22' },
  ],
  documents: [
    { document_id: 'doc_wind_rules_001', title: '风电故障诊断规则库', domain: 'wind_oam', doc_type: 'fault_rule', file_type: 'PDF', file_size: '12.6 MB', version: 'v1.4', status: 'published', chunk_count: 86, embedding_status: 'embedded', vector_index_status: 'indexed', created_at: '2026-06-08 09:10', updated_at: '2026-06-12 09:30', progress: 100 },
    { document_id: 'doc_load_feature_001', title: '区域负荷预测建模规范', domain: 'load_forecast', doc_type: 'feature_engineering', file_type: 'Markdown', file_size: '860 KB', version: 'v0.9', status: 'indexed', chunk_count: 54, embedding_status: 'embedded', vector_index_status: 'indexed', created_at: '2026-06-09 11:00', updated_at: '2026-06-11 16:40', progress: 92 },
    { document_id: 'doc_storage_ems_001', title: '储能 EMS 运行知识库', domain: 'storage_ems', doc_type: 'data_dictionary', file_type: 'PDF', file_size: '8.4 MB', version: 'v1.1', status: 'published', chunk_count: 61, embedding_status: 'embedded', vector_index_status: 'indexed', created_at: '2026-06-10 08:30', updated_at: '2026-06-10 14:05', progress: 100 },
    { document_id: 'doc_calc_logic_001', title: '工业计算逻辑说明', domain: 'calc_logic', doc_type: 'calculation_rule', file_type: 'Markdown', file_size: '420 KB', version: 'v1.0', status: 'metadata_ready', chunk_count: 37, embedding_status: 'pending', vector_index_status: 'pending', created_at: '2026-06-11 10:20', updated_at: '2026-06-12 10:18', progress: 58 },
  ],
  chunks: [
    { chunk_id: 'chunk_wind_accy_001', document_id: 'doc_wind_rules_001', chunk_title: 'AccY 超限与塔架振动风险', chunk_content: 'AccY 是机舱 Y 向加速度，持续超限通常需要结合风速、转速、功率、主频峰值判断塔架共振或机舱横向振动风险。', domain: 'wind_oam', doc_type: 'variable_definition', device_type: 'wind_turbine', scenario: 'fault_diagnosis', fault_type: 'tower_resonance', variables: ['AccY', 'WindSpeed', 'GeneratorSpeed'], token_count: 326, source_file: 'wind_variable_dictionary.md', version: 'v1.4', embedding_status: 'embedded', vector_index_status: 'indexed', score: 0.91, rerank_score: 0.94, created_at: '2026-06-08 09:40', updated_at: '2026-06-12 09:30' },
    { chunk_id: 'chunk_wind_tower_002', document_id: 'doc_wind_rules_001', chunk_title: '塔架一阶共振 FFT 诊断分支', chunk_content: '塔架一阶共振应使用 10 分钟振动窗口进行 0-5Hz 频谱分析，并结合转速区间、功率波动与主频峰值进行人工复核。', domain: 'wind_oam', doc_type: 'fault_rule', device_type: 'wind_turbine', scenario: 'fault_diagnosis', fault_type: 'tower_resonance', variables: ['AccX', 'AccY', 'GridPower'], token_count: 288, source_file: 'wind_fault_rules.pdf', version: 'v1.4', embedding_status: 'embedded', vector_index_status: 'indexed', score: 0.88, rerank_score: 0.9, created_at: '2026-06-08 10:10', updated_at: '2026-06-12 11:20' },
    { chunk_id: 'chunk_load_lag96_001', document_id: 'doc_load_feature_001', chunk_title: 'lag_96 与日周期负荷特征', chunk_content: '15 分钟级负荷序列中 lag_96 表示前一日同一时刻负荷，是区域负荷预测中捕捉日周期的重要特征。', domain: 'load_forecast', doc_type: 'feature_engineering', device_type: 'regional_load', scenario: 'load_forecasting', fault_type: '-', variables: ['LoadPower', 'lag_96', 'rolling_mean_96'], token_count: 402, source_file: 'load_feature_engineering.md', version: 'v0.9', embedding_status: 'embedded', vector_index_status: 'indexed', score: 0.84, rerank_score: 0.82, created_at: '2026-06-09 11:30', updated_at: '2026-06-11 16:40' },
    { chunk_id: 'chunk_storage_soc_001', document_id: 'doc_storage_ems_001', chunk_title: 'SOC 与 SOH 的区别', chunk_content: 'SOC 描述当前剩余电量比例，SOH 描述电池健康状态。EMS 调度需要同时考虑 SOC 安全窗口与 SOH 衰减风险。', domain: 'storage_ems', doc_type: 'data_dictionary', device_type: 'storage_system', scenario: 'storage_ems', fault_type: '-', variables: ['SOC', 'SOH'], token_count: 356, source_file: 'storage_ems_basics.pdf', version: 'v1.1', embedding_status: 'embedded', vector_index_status: 'indexed', score: 0.86, rerank_score: 0.87, created_at: '2026-06-10 09:12', updated_at: '2026-06-10 14:05' },
  ],
  metadata: [
    { name: 'wind_oam', type: 'domain', doc_count: 8, chunk_count: 183, updated_at: '2026-06-12' },
    { name: 'load_forecast', type: 'domain', doc_count: 5, chunk_count: 96, updated_at: '2026-06-11' },
    { name: 'storage_ems', type: 'domain', doc_count: 6, chunk_count: 88, updated_at: '2026-06-10' },
    { name: 'calc_logic', type: 'domain', doc_count: 4, chunk_count: 51, updated_at: '2026-06-12' },
    { name: 'fault_rule', type: 'doc_type', doc_count: 3, chunk_count: 72, updated_at: '2026-06-12' },
    { name: 'tower_resonance', type: 'fault_type', doc_count: 2, chunk_count: 19, updated_at: '2026-06-12' },
    { name: 'AccY', type: 'variables', doc_count: 3, chunk_count: 21, updated_at: '2026-06-12' },
    { name: 'SOC', type: 'variables', doc_count: 2, chunk_count: 16, updated_at: '2026-06-10' },
  ],
  vectorConfig: {
    embedding: { model: 'bge-large-zh-v1.5', mode: 'local', dimension: 1024, batchSize: 32, status: 'ready' },
    vectorDb: { engine: 'Milvus / Chroma / FAISS / Qdrant', indexName: 'energy_oam_chunks', indexedChunks: 386, failedChunks: 3, lastIndexTime: '2026-06-12 19:20' },
    bm25: { enabled: true, backend: 'local bm25', indexedDocs: 27, indexedChunks: 386 },
    hybrid: { vectorTopK: 20, bm25TopK: 20, fusionMethod: 'RRF', finalTopK: 8 },
    rerank: { model: 'bge-reranker-v2-m3', enabled: true, topN: 8, scoreThreshold: 0.62 },
    jobs: [
      { job_id: 'job-001', document_id: 'doc_wind_rules_001', step: 'publish', progress: 100, status: 'completed', logs: 'published to retrieval API', started_at: '2026-06-12 18:10', finished_at: '2026-06-12 18:18' },
      { job_id: 'job-002', document_id: 'doc_load_feature_001', step: 'index', progress: 92, status: 'running', logs: 'building hybrid index', started_at: '2026-06-12 19:10', finished_at: '-' },
      { job_id: 'job-003', document_id: 'doc_calc_logic_001', step: 'embed', progress: 58, status: 'queued', logs: 'waiting for embedding worker', started_at: '-', finished_at: '-' },
    ],
  },
  runtime: {
    layers: ['Control Plane', 'RAG Runtime', 'Evaluation', 'API Layer'],
    mapping: [
      ['Document', 'Document'],
      ['Chunk', 'Node'],
      ['Metadata', 'Node metadata'],
      ['Vector Index', 'VectorStoreIndex'],
      ['BM25 检索', 'BM25Retriever'],
      ['混合检索', 'QueryFusionRetriever / 自定义 RRF'],
      ['Rerank', 'Node Postprocessor / Reranker'],
      ['问答引擎', 'QueryEngine'],
      ['引用来源', 'Source Nodes'],
      ['评估集', 'RAGAS dataset / 自定义 eval dataset'],
    ],
    pipeline: ['Document', 'Loader / Parser', 'EnergyNodeParser', 'Metadata Extractor', 'Embedding', 'VectorStoreIndex', 'Retriever', 'BM25 Retriever', 'Hybrid Retriever', 'RRF Fusion', 'Reranker', 'Query Engine', 'Response Synthesizer', 'Citation Builder'],
    retrievalFlow: ['query', 'intent/domain router', 'metadata filter', 'vector top_k=20', 'bm25 top_k=20', 'RRF top_k=15', 'reranker top_k=5', 'context builder', 'LLM response'],
    parserRules: [
      '风电故障规则：一个故障类型或诊断分支一个 Node',
      '风机变量字典：一个变量一个 Node',
      '风电故障案例：一个案例一个 Node',
      '区域负荷预测知识：一个主题一个 Node',
      '储能 EMS 知识：一个概念或业务场景一个 Node',
      '工业计算逻辑：一个规则案例一个 Node',
      '报告模板：一个报告模块一个 Node',
    ],
  },
  evaluation: {
    metric_cards: [
      {
        key: 'faithfulness',
        title: 'Faithfulness 忠实度',
        evaluation_object: '答案可信度',
        current_avg: 0.87,
        previous_avg: 0.83,
        delta: 0.04,
        trend: 'up',
        formula: '被上下文支持的声明数 / 答案中的总声明数',
        definition: '评估生成答案是否被 retrieved_contexts 支持，用于判断答案是否存在幻觉。',
        low_score_signals: ['回答包含上下文没有的故障原因', '编造设备参数、收益、电价或模型结果', '表格中不存在的数据被说成存在'],
        optimization_methods: ['加强 Prompt 证据约束', '启用无证据拒答', '强制引用 source_chunk_id', '开启 Context Compression'],
      },
      {
        key: 'answer_relevancy',
        title: 'Answer Relevancy 答案相关性',
        evaluation_object: '答案贴题度',
        current_avg: 0.84,
        previous_avg: 0.80,
        delta: 0.04,
        trend: 'up',
        formula: '答案能否反推出原始问题的语义匹配度',
        definition: '评估生成答案是否真正回答了用户问题。',
        low_score_signals: ['问变量定义却回答成诊断流程', '回答大量无关背景', '没有正面回答用户问题'],
        optimization_methods: ['增加 Query Rewrite', '启用 Intent Router', 'Prompt 要求先给结论', '补充 Few-shot 示例'],
      },
      {
        key: 'context_precision',
        title: 'Context Precision 检索精确度',
        evaluation_object: '检索排序',
        current_avg: 0.81,
        previous_avg: 0.74,
        delta: 0.07,
        trend: 'up',
        formula: '相关 chunk 在 Top-K 中的排序质量',
        definition: '评估检索到的 context 中，相关 chunk 是否排在前面。',
        low_score_signals: ['相关 chunk 没排到前面', '风电问题召回储能或负荷预测文档', '变量名和专业缩写匹配不准'],
        optimization_methods: ['加强 Metadata Filter', '启用 Vector + BM25', '使用 RRF 融合', '启用 Rerank'],
      },
      {
        key: 'context_recall',
        title: 'Context Recall 检索召回率',
        evaluation_object: '检索覆盖',
        current_avg: 0.78,
        previous_avg: 0.72,
        delta: 0.06,
        trend: 'up',
        formula: '必要参考上下文被召回的完整度',
        definition: '评估回答问题所需的关键上下文是否被完整召回。',
        low_score_signals: ['必要证据没有被召回', '多跳问题只召回部分链路', '表格被切碎或父文档上下文未带回'],
        optimization_methods: ['提高 top_k', '启用多查询检索', '启用 Query Decomposition', '启用 Parent Document Retriever'],
      },
    ],
    datasets: [
      { dataset_id: 'wind_fault_qa', name: '风电故障 QA', domain: 'wind_oam', sample_count: 2, samples: [
        { eval_id: 'wind_fault_eval_001', question: 'AccY 超限一般可能对应哪些风机振动问题？', ground_truth: 'AccY 超限可能与机舱横向振动、塔架一阶共振、偏航激励等有关，需要结合频域、工况和故障前后窗口判断。', reference_context_ids: ['chunk_wind_accy_001', 'chunk_wind_tower_002'], domain: 'wind_oam', doc_type: 'fault_rule', difficulty: 'medium', tags: ['AccY', 'tower_resonance', 'fault_diagnosis'], created_at: '2026-06-11', updated_at: '2026-06-24' },
      ] },
      { dataset_id: 'load_forecast_qa', name: '负荷预测 QA', domain: 'load_forecast', sample_count: 1, samples: [
        { eval_id: 'load_forecast_eval_001', question: 'lag_96 在负荷预测中代表什么？', ground_truth: '15 分钟级负荷序列中 lag_96 表示前一日同一时刻负荷，用于捕捉日周期。', reference_context_ids: ['chunk_load_lag96_001'], domain: 'load_forecast', doc_type: 'feature_engineering', difficulty: 'easy', tags: ['lag_96'], created_at: '2026-06-11', updated_at: '2026-06-24' },
      ] },
      { dataset_id: 'storage_ems_qa', name: '储能 EMS QA', domain: 'storage_ems', sample_count: 1, samples: [
        { eval_id: 'storage_ems_eval_001', question: 'SOC 和 SOH 有什么区别？', ground_truth: 'SOC 描述当前剩余电量比例，SOH 描述电池健康状态，EMS 调度需要同时考虑二者。', reference_context_ids: ['chunk_storage_soc_001'], domain: 'storage_ems', doc_type: 'data_dictionary', difficulty: 'easy', tags: ['SOC', 'SOH'], created_at: '2026-06-11', updated_at: '2026-06-24' },
      ] },
      { dataset_id: 'calc_logic_qa', name: '工业计算逻辑 QA', domain: 'calc_logic', sample_count: 1, samples: [
        { eval_id: 'calc_logic_eval_001', question: '绘制故障前机舱加速度 0-5Hz 频谱应该生成什么计算逻辑？', ground_truth: '应选择故障前窗口，取机舱加速度变量并执行 0-5Hz FFT 或能量谱计算，保留单位与窗口说明。', reference_context_ids: ['chunk_calc_fft_001'], domain: 'calc_logic', doc_type: 'calculation_rule', difficulty: 'hard', tags: ['FFT', 'WINDOW', 'AXIS'], created_at: '2026-06-11', updated_at: '2026-06-24' },
      ] },
      { dataset_id: 'mixed_energy_qa', name: '混合能源 QA', domain: 'mixed_energy', sample_count: 1, samples: [
        { eval_id: 'mixed_energy_eval_001', question: '负荷预测如何服务储能削峰填谷？', ground_truth: '负荷预测为 EMS 提供未来负荷走势，结合 SOC、PCS 功率约束和电价策略生成充放电计划。', reference_context_ids: ['chunk_load_lag96_001', 'chunk_storage_soc_001'], domain: 'mixed_energy', doc_type: 'cross_domain_qa', difficulty: 'medium', tags: ['load_forecast', 'storage_ems'], created_at: '2026-06-11', updated_at: '2026-06-24' },
      ] },
    ],
    runs: [
      { run_id: 'ragas_run_20260624_001', eval_dataset: 'energy_rag_eval.jsonl', retrieval_config: 'hybrid+parent+rerank+compression', retriever_config: 'hybrid + metadata + parent-child + rerank + compression', embedding_model: 'bge-large-zh', vector_db: 'chroma', reranker_model: 'score-fallback', top_k: 10, score_threshold: 0.62, status: 'completed', sample_count: 11, started_at: '2026-06-24 20:00', finished_at: '2026-06-24 20:08', avg_faithfulness: 0.87, avg_answer_relevancy: 0.84, avg_context_precision: 0.81, avg_context_recall: 0.78 },
      { run_id: 'ragas_run_20260623_001', eval_dataset: 'energy_rag_eval.jsonl', retrieval_config: 'hybrid+metadata', retriever_config: 'hybrid + metadata', embedding_model: 'bge-large-zh', vector_db: 'chroma', reranker_model: 'disabled', top_k: 8, score_threshold: 0.58, status: 'completed', sample_count: 11, started_at: '2026-06-23 20:00', finished_at: '2026-06-23 20:05', avg_faithfulness: 0.83, avg_answer_relevancy: 0.80, avg_context_precision: 0.74, avg_context_recall: 0.72 },
    ],
    metrics: [
      { name: 'faithfulness', value: 0.87, group: 'RAGAS' },
      { name: 'answer_relevancy', value: 0.84, group: 'RAGAS' },
      { name: 'context_precision', value: 0.81, group: 'RAGAS' },
      { name: 'context_recall', value: 0.78, group: 'RAGAS' },
      { name: 'hit@k', value: 0.9, group: '工业指标' },
      { name: 'mrr', value: 0.73, group: '工业指标' },
      { name: 'source_citation_rate', value: 0.96, group: '工业指标' },
      { name: 'latency_ms', value: 1240, group: '工程指标' },
    ],
    sample_results: [
      {
        run_id: 'ragas_run_20260624_001',
        eval_id: 'wind_fault_eval_001',
        question: 'AccY 超限一般可能对应哪些风机振动问题？',
        ground_truth: 'AccY 超限可能与机舱横向振动、塔架一阶共振、偏航激励等有关，需要结合频域、工况和故障前后窗口判断。',
        retrieved_contexts: ['AccY 是机舱 Y 向加速度，持续超限通常需要结合风速、转速、功率、主频峰值判断塔架共振或机舱横向振动风险。', '塔架一阶共振应使用 10 分钟振动窗口进行 0-5Hz 频谱分析，并结合转速区间、功率波动与主频峰值进行人工复核。'],
        retrieved_chunk_ids: ['chunk_wind_accy_001', 'chunk_wind_tower_002'],
        reference_context_ids: ['chunk_wind_accy_001', 'chunk_wind_tower_002'],
        generated_answer: 'AccY 超限通常说明机舱横向振动增强，需结合塔架一阶共振规则、0-5Hz 频谱和工况窗口复核。',
        metrics: { faithfulness: 0.92, answer_relevancy: 0.88, context_precision: 0.84, context_recall: 0.90 },
        failure_type: null,
        optimization_suggestion: '当前样本表现正常，继续保持 hybrid + rerank + compression 配置。',
        source_chunks: ['chunk_wind_accy_001', 'chunk_wind_tower_002'],
        trace: { trace_id: 'trace_demo_001', retrieval_mode: 'hybrid', latency_ms: { total: 1180 } },
      },
      {
        run_id: 'ragas_run_20260624_001',
        eval_id: 'calc_logic_eval_001',
        question: '绘制故障前机舱加速度 0-5Hz 频谱应该生成什么计算逻辑？',
        ground_truth: '应选择故障前窗口，取机舱加速度变量并执行 0-5Hz FFT 或能量谱计算，保留单位与窗口说明。',
        retrieved_contexts: ['AccY 是机舱 Y 向加速度，可用于振动分析。'],
        retrieved_chunk_ids: ['chunk_wind_accy_001'],
        reference_context_ids: ['chunk_calc_fft_001'],
        generated_answer: '选择 AccY 后绘制趋势即可。',
        metrics: { faithfulness: 0.86, answer_relevancy: 0.67, context_precision: 0.48, context_recall: 0.42 },
        failure_type: 'retrieval_miss',
        optimization_suggestion: '必要计算规则未召回，建议提高 top_k、增加 Query Decomposition，并补强 calc_logic metadata filter。',
        source_chunks: ['chunk_wind_accy_001'],
        trace: { trace_id: 'trace_demo_002', retrieval_mode: 'hybrid', latency_ms: { total: 1320 } },
      },
    ],
    failures: [
      { question: '绘制故障前机舱加速度 0-5Hz 频谱应该生成什么计算逻辑？', domain: 'calc_logic', ground_truth: '应选择故障前窗口，取机舱加速度变量并执行 0-5Hz FFT 或能量谱计算。', actual_answer: '选择 AccY 后绘制趋势即可。', expected_chunk_ids: ['chunk_calc_fft_001'], retrieved_chunk_ids: ['chunk_wind_accy_001'], faithfulness: 0.86, answer_relevancy: 0.67, context_precision: 0.48, context_recall: 0.42, failure_type: 'retrieval_miss', root_cause: '必要参考 chunk 未召回：chunk_calc_fft_001。', optimization_suggestion: '提高 top_k、增加 Query Decomposition、补强 calc_logic metadata filter。' },
      { question: 'lag_96 在负荷预测中代表什么？', domain: 'load_forecast', ground_truth: '前一日同一时刻负荷。', actual_answer: '介绍了整体模型选择，但没有解释 lag_96。', expected_chunk_ids: ['chunk_load_lag96_001'], retrieved_chunk_ids: ['chunk_load_model_002', 'chunk_load_quality_001'], faithfulness: 0.78, answer_relevancy: 0.52, context_precision: 0.58, context_recall: 0.66, failure_type: 'off_topic', root_cause: '答案没有正面解释变量定义，检索结果也偏向建模方法。', optimization_suggestion: '增强变量名 BM25 匹配，Query Rewrite 明确“变量定义”。' },
    ],
    failure_types: [
      { type: 'retrieval_miss', description: '必要参考 chunk 没有被召回。', metric: 'Context Recall' },
      { type: 'bad_ranking', description: '相关 chunk 被召回了，但排序太靠后。', metric: 'Context Precision' },
      { type: 'noisy_context', description: 'Top-K 中混入大量无关 chunk。', metric: 'Context Precision / Faithfulness' },
      { type: 'hallucination', description: '答案包含 retrieved_contexts 不支持的内容。', metric: 'Faithfulness' },
      { type: 'off_topic', description: '答案偏题，没有正面回答用户问题。', metric: 'Answer Relevancy' },
      { type: 'incomplete_answer', description: '答案只回答了一部分。', metric: 'Answer Relevancy / Context Recall' },
      { type: 'wrong_domain', description: '检索到了错误知识域。', metric: 'Context Precision' },
      { type: 'bad_citation', description: '引用来源错误或缺失。', metric: 'Faithfulness / source_citation' },
    ],
    metric_guide: [
      { metric: 'Context Precision', object: '检索排序', low_score: '相关 chunk 排名靠后，无关 chunk 混入前列', optimization: 'Metadata Filter、混合检索、RRF、Rerank、优化 chunk 粒度、优化 embedding' },
      { metric: 'Context Recall', object: '检索覆盖', low_score: '必要证据没召回，多跳信息缺失', optimization: '提高 top_k、多查询、Query Decomposition、父子文档索引、表格恢复、Graph-enhanced Retrieval' },
      { metric: 'Faithfulness', object: '答案可信度', low_score: '回答包含上下文不支持的内容', optimization: 'Prompt 约束、引用来源、无证据拒答、Context Compression、结构化输出' },
      { metric: 'Answer Relevancy', object: '答案贴题度', low_score: '回答偏题、啰嗦、没有正面回答', optimization: 'Query Rewrite、Intent Router、Few-shot、输出模板、拒绝无关扩展' },
    ],
    energy_scenarios: [
      { domain: 'wind_oam', name: '风电故障诊断', metric_notes: { 'Context Precision': '是否把 AccY 变量、塔架共振规则、故障案例排在前面。', 'Context Recall': '是否同时召回变量解释、故障规则、案例证据和运维建议。', Faithfulness: '是否只根据规则和案例回答，避免编造故障原因。', 'Answer Relevancy': '是否直接回答可能故障、需查看变量和人工复核方式。' } },
      { domain: 'load_forecast', name: '区域负荷预测', metric_notes: { 'Context Precision': '是否把 lag_96、rolling_mean_96、数据质量规则、模型方法排在前面。', 'Context Recall': '是否召回数据字典、特征工程、预测模型、评估指标和业务应用。', Faithfulness: '是否避免编造天气字段、节假日字段和模型训练结果。', 'Answer Relevancy': '是否围绕特征、模型、误差和 EMS 应用回答。' } },
      { domain: 'storage_ems', name: '储能 EMS', metric_notes: { 'Context Precision': '是否优先召回 EMS、BMS、PCS、SOC、SOH、削峰填谷相关 chunk。', 'Context Recall': '是否覆盖负荷预测、SOC 约束、PCS 功率约束和充放电策略。', Faithfulness: '是否避免编造储能收益、电价规则和设备参数。', 'Answer Relevancy': '是否直接解释 EMS 场景，而不是泛泛讲能源管理。' } },
      { domain: 'calc_logic', name: '工业计算逻辑', metric_notes: { 'Context Precision': '是否召回 WINDOW、STAT、FFT、AXIS 等正确计算规则。', 'Context Recall': '是否召回变量规则、计算表达式、单位规则和纠错规则。', Faithfulness: '是否避免生成知识库没有定义的计算表达式。', 'Answer Relevancy': '是否直接回答绘图、统计、频谱或纠错逻辑。' } },
    ],
    trend: [
      { date: '2026-06-20', faithfulness: 0.78, answer_relevancy: 0.76, context_precision: 0.68, context_recall: 0.66 },
      { date: '2026-06-21', faithfulness: 0.80, answer_relevancy: 0.78, context_precision: 0.70, context_recall: 0.68 },
      { date: '2026-06-22', faithfulness: 0.82, answer_relevancy: 0.79, context_precision: 0.72, context_recall: 0.70 },
      { date: '2026-06-23', faithfulness: 0.83, answer_relevancy: 0.80, context_precision: 0.74, context_recall: 0.72 },
      { date: '2026-06-24', faithfulness: 0.87, answer_relevancy: 0.84, context_precision: 0.81, context_recall: 0.78 },
    ],
    config_comparisons: [
      { config_name: 'Vector + BGE', embedding_model: 'bge-large-zh', retrieval_mode: 'vector', vector_top_k: 20, bm25_top_k: 0, rerank_enabled: false, faithfulness: 0.79, answer_relevancy: 0.77, context_precision: 0.66, context_recall: 0.69, avg_latency_ms: 820 },
      { config_name: 'Hybrid + Metadata', embedding_model: 'bge-large-zh', retrieval_mode: 'hybrid', vector_top_k: 20, bm25_top_k: 20, rerank_enabled: false, faithfulness: 0.83, answer_relevancy: 0.80, context_precision: 0.74, context_recall: 0.72, avg_latency_ms: 980 },
      { config_name: 'Parent-Child + Rerank + Compression', embedding_model: 'bge-large-zh', retrieval_mode: 'parent_child', vector_top_k: 20, bm25_top_k: 20, rerank_enabled: true, faithfulness: 0.87, answer_relevancy: 0.84, context_precision: 0.81, context_recall: 0.78, avg_latency_ms: 1240 },
      { config_name: 'RAG-Fusion + Rerank', embedding_model: 'bge-large-zh', retrieval_mode: 'rag_fusion', vector_top_k: 24, bm25_top_k: 24, rerank_enabled: true, faithfulness: 0.88, answer_relevancy: 0.85, context_precision: 0.80, context_recall: 0.82, avg_latency_ms: 1580 },
    ],
    automatic_diagnosis: {
      primary_failure_type: null,
      root_cause: '四个核心指标均处于可接受区间。',
      suggestions: ['保持当前检索配置', '继续扩充覆盖低频问题的评测样本', '定期观察趋势和失败样本'],
      combined_findings: [],
    },
  },
  reports: [
    { id: 'rp-001', title: 'WTG-A05 变桨系统异常诊断报告', type: '风电故障诊断报告', scenario: '风电故障复盘', inputData: 'SCADA 摘要 + 振动特征', sources: ['chunk_wind_accy_001', 'chunk_wind_tower_002'], status: 'generated', generatedAt: '2026-06-12 17:30', summary: '疑似编码器反馈异常并伴随振动风险，建议现场复核线缆屏蔽、接插件与驱动器日志。' },
    { id: 'rp-002', title: '华东区域 15 分钟负荷分析报告', type: '区域负荷分析报告', scenario: '负荷预测特征解释', inputData: '15 分钟负荷序列', sources: ['chunk_load_lag96_001'], status: 'generated', generatedAt: '2026-06-11 20:10', summary: '午峰负荷受高温影响抬升，商业区负荷弹性高于居民区。' },
    { id: 'rp-003', title: '储能 EMS 日运行简报', type: '储能 EMS 运行日报', scenario: '削峰填谷运行复盘', inputData: 'SOC / SOH / PCS 功率', sources: ['chunk_storage_soc_001'], status: 'generating', generatedAt: '2026-06-14 08:00', summary: '削峰填谷策略执行稳定，SOC 保持在安全窗口内。' },
  ],
};

// Demo fixtures above are examples only; disconnected pages start without fabricated records.
export function createEmptyEvaluation() {
  return {
    metric_cards: demoState.evaluation.metric_cards.map((metric) => ({
      ...metric, current_avg: null, previous_avg: null, delta: null, trend: null,
    })),
    datasets: [], runs: [], metrics: [], sample_results: [], failures: [], trend: [], config_comparisons: [],
    failure_types: demoState.evaluation.failure_types,
    metric_guide: demoState.evaluation.metric_guide,
    energy_scenarios: demoState.evaluation.energy_scenarios,
    automatic_diagnosis: null,
  };
}

export function createEmptyState() {
  return {
    domains: demoState.domains,
    runtime: demoState.runtime,
    overview: { kpis: [], pipelineStatus: [], recentDocumentJobs: [], recentTests: [] },
    knowledge: [], documents: [], chunks: [], metadata: [],
    vectorConfig: {
      embedding: { model: '—', mode: '—', dimension: '—', batchSize: '—', status: '未加载' },
      vectorDb: { engine: '—', indexName: '—', indexedChunks: '—', failedChunks: '—' },
      bm25: { enabled: false, backend: '未加载' },
      hybrid: { vectorTopK: '—', bm25TopK: '—', fusionMethod: '—' },
      rerank: { model: '—', enabled: false, topN: '—', scoreThreshold: '—' },
      jobs: [],
    },
    evaluation: createEmptyEvaluation(),
  };
}

export const apiSections = [
  { group: 'Document API', items: ['POST /api/documents/upload', 'GET /api/documents', 'POST /api/documents/{document_id}/process', 'DELETE /api/documents/{document_id}', 'POST /api/v2/ingest'] },
  { group: 'Config API', items: ['GET /api/v2/config/options', 'GET /api/vector-index', 'GET /api/state'] },
  { group: 'Query & Trace API', items: ['POST /api/v2/query/test', 'GET /api/v2/traces/{trace_id}', 'POST /api/retrieval/search', 'POST /api/context/build'] },
  { group: 'Evaluation API', items: ['GET /api/v1/evaluation/datasets', 'POST /api/v1/evaluation/run', 'GET /api/v1/evaluation/runs', 'GET /api/v1/evaluation/metrics/summary', 'POST /api/v1/evaluation/diagnose', 'POST /api/v2/eval/run', 'POST /api/v2/eval/upload', 'GET /api/v2/eval/reports/{filename}', 'POST /api/v2/experiments/run'] },
  { group: 'Graph Retrieval API', items: ['GET /api/v2/graph/status', 'POST /api/v2/graph/build', 'POST /api/v2/graph/query', 'GET /api/v2/graph/entities', 'GET /api/v2/graph/relations'] },
];

export const contextApiExample = {
  request: `{
  "query": "AccY 超限一般可能对应哪些风机振动问题？",
  "domains": ["wind_oam"],
  "filters": {
    "doc_type": ["fault_rule", "variable_definition", "fault_case"],
    "variables": ["AccY"]
  },
  "top_k": 5,
  "max_tokens": 3000
}`,
  response: `{
  "query": "AccY 超限一般可能对应哪些风机振动问题？",
  "context_text": "【片段1】AccY 是机舱 Y 向加速度...\\n【片段2】塔架共振通常表现为...",
  "sources": [
    {
      "chunk_id": "chunk_wind_accy_001",
      "source_file": "wind_variable_dictionary.md",
      "domain": "wind_oam",
      "doc_type": "variable_definition"
    }
  ],
  "token_count": 1820
}`,
};

export const sdkExamples = [
  { title: '可配置单问题测试', code: `trace = requests.post("http://127.0.0.1:8000/api/v2/query/test", json={\n  "question": "塔架一阶共振怎么判断？",\n  "options": {"retrieval_mode": "hybrid", "rerank": True, "compression": True}\n}).json()` },
  { title: '批量评测与报告', code: `report = requests.post("http://127.0.0.1:8000/api/v2/eval/run", json={\n  "eval_set": "data/eval_sets/energy_rag_eval.jsonl",\n  "experiment_name": "hybrid_parent_rerank"\n}).json()` },
  { title: 'CLI 实验对比', code: `python -m app.main experiment --config app/config/experiment_config.yaml` },
];

export const chatScenes = [
  { name: '风电故障问答', scenario: 'wind_oam', questions: ['AccY 超限一般可能对应哪些风机振动问题？', '塔架一阶共振如何判断？', '过速故障需要看哪些变量？'] },
  { name: '区域负荷预测问答', scenario: 'load_forecast', questions: ['15 分钟级区域负荷预测应该构造哪些特征？', 'lag_96 在负荷预测中代表什么？', '区域负荷数据质量检查包括哪些内容？'] },
  { name: '储能 EMS 问答', scenario: 'storage_ems', questions: ['EMS、BMS、PCS 分别是什么？', '负荷预测如何服务储能削峰填谷？', 'SOC 和 SOH 有什么区别？'] },
  { name: '工业计算逻辑问答', scenario: 'calc_logic', questions: ['绘制故障前机舱加速度 0-5Hz 频谱应该生成什么计算逻辑？', '总扭矩如何由原始变量计算？', '什么情况下才使用双轴 AXIS？'] },
];
