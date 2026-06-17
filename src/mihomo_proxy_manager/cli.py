from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config


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


def _cmd_check(config_path: str) -> int:
    config_file = Path(config_path)
    config = load_config(config_file, validate=False)
    report = config.validate(config_path=config_file)
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")
    if report.ok:
        print("OK: configuration is valid")
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return _cmd_check(args.config)
    return 0
