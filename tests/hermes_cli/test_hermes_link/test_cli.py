from hermes_cli.hermes_link.commands import build_link_parser
from hermes_cli.main import _BUILTIN_SUBCOMMANDS


def test_link_is_builtin_and_parser_is_backward_compatible():
    import argparse

    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers(dest="command")
    build_link_parser(subparsers)
    args = root.parse_args(["link", "chat", "hello", "--json"])
    assert args.command == "link"
    assert args.link_command == "chat"
    assert args.message == "hello"
    assert "link" in _BUILTIN_SUBCOMMANDS
