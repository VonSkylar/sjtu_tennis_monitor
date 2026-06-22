"""Entry point for ``python -m sjtu_tennis_toolkit``."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="交我办网球场监控与抢场工具集 — SJTU Tennis Toolkit",
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
        from sjtu_tennis_toolkit.gui.pc_app import main as pc_main
        pc_main()
    elif args.rush:
        from sjtu_tennis_toolkit.gui.rush_app import main as rush_main
        rush_main()
    else:
        from sjtu_tennis_toolkit.gui.browser_app import main as browser_main
        browser_main()


if __name__ == "__main__":
    main()
