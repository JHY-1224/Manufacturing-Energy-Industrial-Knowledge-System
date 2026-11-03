#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv-retrieval/bin/python ]]; then
  print '本地 Python 环境尚未准备，请按 AGENTS.md 完成初始化。'
  read '?按回车关闭'
  exit 1
fi
.venv-retrieval/bin/python scripts/start_workbench.py
if [[ $? -ne 0 ]]; then
  read '?启动失败，按回车关闭；错误日志与原始资料均已保留。'
fi
