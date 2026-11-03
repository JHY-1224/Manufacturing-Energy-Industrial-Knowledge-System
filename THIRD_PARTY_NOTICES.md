# 许可证与公开内容清单

本项目原创源码以根目录 `LICENSE` 所载 **GNU Affero General Public License version 3 only（AGPL-3.0-only）** 发布。未向任何依赖、模型或第三方资料重新授予许可。完整开发档案见 `AGENTS.md`，依赖逐项版本和原始许可证元数据见 `docs/dependency-inventory.json`。

## 软件依赖

| 范围 | 主要组件 | 许可证元数据 |
|---|---|---|
| Web/API | Vue、Vite、Vue 插件、FastAPI、Pydantic、PyYAML | MIT；Uvicorn/Starlette 为 BSD-3-Clause |
| 工作流 | LangGraph、LangChain Core、LangSmith 等 | 以逐包清单为准，主要为 MIT |
| 文档 | PyMuPDF/MuPDF 1.26.7 | AGPL-3.0 或 Artifex 商业许可 |
| Office | python-docx、openpyxl | MIT；lxml 及其解析库按发行包自身通知 |
| 图片 | Pillow 13.0.0 | MIT-CMU；所链接第三方编解码库按其发行包通知 |
| 本地推理（可选独立环境） | MLX/MLX-LM/MLX-VLM、PyTorch、Transformers | 具体版本、许可证和项目链接见逐包清单 |

PyMuPDF 的双许可依据：[Artifex 官方许可页](https://pymupdf.io/licensing)。本源码按 AGPL 发布，不把“可选安装”解释为许可豁免。闭源改造或交付须自行确认是否满足相关许可，必要时取得商业许可。

本仓库不携带 Python wheels、node_modules、编解码库、虚拟环境或构建二进制；安装时须保留各发行包的 LICENSE、COPYING 和 NOTICE。依赖清单保留上游元数据原样的许可标识，未声明项明确显示未知；多行全文仅保留首行，完整条款以发行包为准。清单覆盖声明的 Python 配置和 npm 锁定依赖，不把 Mac 专用轮子视为跨平台验证结果。

## 预训练模型（不随源码分发）

| 用途 | 官方模型与固定版本 | 官方条款 |
|---|---|---|
| 文本向量 | [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5/tree/7999e1d3359715c523056ef9478215996d62a620) | MIT，以该版本模型卡/许可为准 |
| 文本重排 | [BAAI/bge-reranker-base](https://huggingface.co/BAAI/bge-reranker-base/tree/2cfc18c9415c912f9d8155881c133215df768a70) | MIT，以该版本模型卡/许可为准 |
| 跨模态向量 | [google/siglip2-base-patch16-224](https://huggingface.co/google/siglip2-base-patch16-224/tree/75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2) | Apache-2.0 |
| 文本生成 | [Qwen/Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B) 的本机 text-only MLX4bit 推理副本 | Apache-2.0；本机原始索引指纹与转换收据见 AGENTS |
| 图片描述 | [Qwen/Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) 的本机 MLX4bit 推理副本 | Apache-2.0；本机来源和转换收据保留，不冒充固定上游 commit |

量化转换是推理部署准备，没有训练、微调或 LoRA。普通使用可配置自己合法取得的预训练模型；项目启动不自动下载权重。Apple Vision OCR 使用 macOS 系统框架，不作为本项目开源软件分发。

## 知识资料与测试材料

`tests/fixtures/`、示例评测数据及 UI fixture 为项目合成材料。独立模型服务测试使用受控合成响应，不构成真实模型准确率。

六行业来源文件、化妆品专属资料、来源网页/图片、第三方表格和公式原图、真实问答和审核数据库均仅在本机保存。即使来源可公开访问，也不自动获得再分发权；这些内容及模型权重**不进入 GitHub 当前源码树**。文档中的汇总数量、实现和验证过程不授予原始资料许可。

## 检查与边界

`python scripts/build_dependency_inventory.py` 在已准备的环境从发行包元数据重建清单；没有对应环境时会明确报告缺失，不安装依赖。`python scripts/check_public_package.py` 核对源码白名单、文本类型、常见凭据和个人主目录路径；还需人工阅读准备公开的内容。它不扫描私有运行目录，不承诺识别所有秘密，不将旧提交历史重写为已清理。

根目录 LICENSE 原文来自 [MuPDF 1.26.7 官方 COPYING](https://github.com/ArtifexSoftware/mupdf/blob/1.26.7/COPYING)，对应 [GNU AGPLv3](https://www.gnu.org/licenses/agpl-3.0.html)。保留其原文，不把许可证文本的版权声明替换为项目声明。
