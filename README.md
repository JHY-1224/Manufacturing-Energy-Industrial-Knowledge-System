# 制造能源工业知识库系统

面向汽车、高端装备、电子、消费品、材料和能源的本地知识库。支持资料入库、复杂文档解析、图文检索、带来源问答、任务续跑、版本差异、图文区域对照及质量排错。

**只建设知识库，使用现有预训练模型推理，不训练、不微调。** 仓库已由 `General-Energy-RAG-System` 更名为 [`Manufacturing-Energy-Industrial-Knowledge-System`](https://github.com/JHY-1224/Manufacturing-Energy-Industrial-Knowledge-System)，提交历史保留。

## 开始使用

需要 Python 3.12、Node.js 22.12 或更新的受支持版本。以下命令在项目根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-base.lock.txt -r backend/requirements-parsers.txt -r backend/requirements-workbench.txt
npm ci
python scripts/manage_workbench.py check
python scripts/manage_workbench.py start --mode offline
```

打开 http://127.0.0.1:8080 。`offline` 可检查入库、编辑、界面与检索管线，使用确定性 Hash 向量，不代表真实语义模型效果。真实问答、重排和图文语义检索需按 [AGENTS.md](AGENTS.md#已锁定的本机模型与环境) 配置既有本地模型服务，或使用自己批准的服务端配置；配置失败明确报错。

```bash
python scripts/manage_workbench.py status
python scripts/manage_workbench.py stop
# 仅在已有独立环境和本地模型均准备完毕时：
.venv-retrieval/bin/python scripts/manage_workbench.py start --mode local
```

自动启停已在 Apple Silicon Mac 验证；Windows 使用两个终端分别运行 `python backend/main.py`、`npm run dev`，按 Ctrl+C 停止。不要在公网直接暴露本地工作台。

## 文档与公开范围

- [AGENTS.md](AGENTS.md)：唯一完整的功能、架构、配置和开发验证档案，最新记录位于文末。
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)：许可证、模型及资料边界；[依赖清单](docs/dependency-inventory.json) 提供具体版本和元数据来源。
- 本仓库包含源码、配置模板和合成测试材料。**不包含本机行业原件、私有资料、真实数据库、索引、模型权重或凭据**；文档中的本机资料数量不是克隆后的初始内容。
- `python scripts/check_public_package.py` 检查准备公开的文件；`python -m unittest discover -s tests -v`、`node --test tests/*.test.mjs` 和 `npm run build -- --emptyOutDir=false` 执行回归。

源码采用 [AGPL-3.0-only](LICENSE)。第三方依赖、模型和原始资料分别遵循其自身条款。本轮按用户要求未执行长时间压力测试、整套备份恢复或干净环境复现；安装命令与 CI 检查不代表这些验收已经完成。
