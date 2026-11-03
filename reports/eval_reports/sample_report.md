# 制造能源工业知识库系统 · 示例评估报告

## Recommended baseline

`metadata filter + hybrid search + parent-child + rerank + compression`

| Metric | Sample value |
|---|---:|
| Hit@5 | 0.91 |
| Recall@5 | 0.86 |
| MRR | 0.78 |
| nDCG@5 | 0.84 |
| Faithfulness | 0.89 |
| Answer relevancy | 0.87 |
| Context precision | 0.82 |
| Context recall | 0.86 |
| Average total latency | 5200 ms |

This file is a format example. Run `python -m app.main experiment --config app/config/experiment_config.yaml` to generate reports from the current local corpus.
