"""
client_core.parser
~~~~~~~~~~~~~~~~~~
Generic argparse builder for Tier 1 Ophix clients.
"""

import argparse
from typing import Any, Mapping


def build_parser(config, commands):
    # type: (Any, Mapping[str, dict]) -> argparse.ArgumentParser
    """
    Build an ArgumentParser from a ClientConfig and COMMANDS dict.

    Each command spec may include:
      help                      — subcommand help string
      hidden                    — if True, suppresses help listing
      arguments                 — list of arg dicts with "name" + add_argument kwargs
      mutually_exclusive_groups — list of {required, arguments} dicts
      handler                   — callable(args), stored as args.func via set_defaults
    """
    parser = argparse.ArgumentParser(prog=config.prog, description=config.description)
    parser.add_argument(
        "--version", action="version",
        version="{} {}".format(config.prog, config.version),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, spec in commands.items():
        help_text = argparse.SUPPRESS if spec.get("hidden") else spec.get("help")
        sub = subparsers.add_parser(name, help=help_text)

        for arg in spec.get("arguments", []):
            arg = arg.copy()
            arg_name = arg.pop("name")
            sub.add_argument(arg_name, **arg)

        for group_spec in spec.get("mutually_exclusive_groups", []):
            group = sub.add_mutually_exclusive_group(required=group_spec.get("required", False))
            for arg in group_spec["arguments"]:
                arg = arg.copy()
                arg_name = arg.pop("name")
                group.add_argument(arg_name, **arg)

        sub.set_defaults(func=spec["handler"])

    return parser


def make_main(config, commands):
    # type: (Any, Mapping[str, dict]) -> Any
    """Return a main() callable for the given config and commands."""
    def main():
        from client_core.core import set_active_config
        set_active_config(config)
        parser = build_parser(config, commands)
        args = parser.parse_args()
        args.func(args)
    return main
