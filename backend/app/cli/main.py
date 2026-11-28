from __future__ import annotations

import argparse
import logging

from . import commands
from .audit import commands as audit_commands
from .evaluation import commands as evaluation_commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(required=True)
    commands.register_pipeline(sub)
    audit_commands.register(sub)
    evaluation_commands.register(sub)
    commands.register_admin(sub)
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
