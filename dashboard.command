#!/bin/sh
# macOS double-click launcher / macOS 雙擊啟動器。
set -eu
cd -- "$(dirname -- "$0")"

if [ -x .venv/bin/python ]; then
    workspace_python=.venv/bin/python
elif command -v python3.13 >/dev/null 2>&1; then
    workspace_python=python3.13
else
    printf '%s\n' '找不到 Python 3.13。請先依 README.md 完成安裝。' '按 Enter 關閉。'
    read -r workspace_reply
    exit 1
fi

if ! "$workspace_python" -c 'import requests, bs4, tqdm' >/dev/null 2>&1; then
    printf '%s\n' '尚未安裝專案相依套件。請先依 README.md 的安裝步驟完成設定。' '按 Enter 關閉。'
    read -r workspace_reply
    exit 1
fi

exec "$workspace_python" -m gui
