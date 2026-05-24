"""Entry point for ``python -m sjtu_tennis_monitor``."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="交我办网球场空位警报器 — SJTU Tennis Court Booking Monitor",
    )
    parser.add_argument(
        "--pc",
        action="store_true",
        help="启动 PC/ADB 版监控器（默认启动浏览器版）",
    )
    args = parser.parse_args()

    if args.pc:
        from sjtu_tennis_monitor.gui.pc_app import main as pc_main
        pc_main()
    else:
        from sjtu_tennis_monitor.gui.browser_app import main as browser_main
        browser_main()


if __name__ == "__main__":
    main()
