"""Launch the local workspace with `python -m gui`."""

from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

from .server import WorkspaceServer


def main() -> int:
    parser = argparse.ArgumentParser(description="Bakusai 本機圖形工作台")
    parser.add_argument(
        "--port", type=int, default=8765, help="本機連接埠（預設 8765；0 為自動選擇）"
    )
    parser.add_argument("--no-browser", action="store_true", help="不要自動開啟瀏覽器")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("連接埠必須介於 0 到 65535")
    logging.basicConfig(level=logging.WARNING)
    try:
        server = WorkspaceServer(args.port)
    except OSError:
        print("無法啟動工作台，連接埠可能已被使用。可改用 python -m gui --port 8766")
        return 1
    print(f"Bakusai 工作台：{server.url}\n資料保留於本機。按 Ctrl+C 結束。", flush=True)
    if not args.no_browser:
        timer = threading.Timer(0.4, webbrowser.open, args=(server.url,))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n工作台已結束。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
