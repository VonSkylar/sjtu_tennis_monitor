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
    parser.add_argument(
        "--rush",
        action="store_true",
        help="启动网页版抢场器",
    )
    args = parser.parse_args()

    if args.pc and args.rush:
        parser.error("--pc 和 --rush 不能同时使用")

    if args.pc:
        from sjtu_tennis_monitor.gui.pc_app import main as pc_main
        pc_main()
    elif args.rush:
        from sjtu_tennis_monitor.gui.rush_app import main as rush_main
        rush_main()
    else:
        from sjtu_tennis_monitor.gui.browser_app import main as browser_main
        browser_main()


if __name__ == "__main__":
    main()
