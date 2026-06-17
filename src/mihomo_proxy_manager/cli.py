from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mpm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="run the provider service")
    serve.add_argument("-c", "--config", required=True)

    check = subparsers.add_parser("check", help="validate configuration")
    check.add_argument("-c", "--config", required=True)

    refresh = subparsers.add_parser("refresh", help="refresh one source")
    refresh.add_argument("-c", "--config", required=True)
    refresh.add_argument("source")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    return 0
