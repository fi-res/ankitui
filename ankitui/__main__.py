"""Command line entry point: `ankitui [--collection PATH]` or `ankitui server ...`."""

from __future__ import annotations

import argparse
import sys

from . import __version__


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["server"]:
        from .server import main as server_main

        server_main(argv[1:])
        return
    parser = argparse.ArgumentParser(prog="ankitui", description="A terminal client for Anki collections.",
        epilog="Run `ankitui server --help` to host your own sync server.",
    )
    parser.add_argument(
        "-c",
        "--collection",
        help="path to the collection file (default: $ANKITUI_COLLECTION or ~/.local/share/ankitui/collection.anki2)",
    )
    icons = parser.add_mutually_exclusive_group()
    icons.add_argument("--icons", dest="icons", action="store_true", default=None, help="use Nerd Font icons")
    icons.add_argument("--no-icons", dest="icons", action="store_false", help="use plain Unicode symbols instead of Nerd Font icons")
    parser.add_argument("--version", action="version", version=f"ankitui {__version__}")
    args = parser.parse_args(argv)

    from .ui.app import AnkiTUI

    AnkiTUI(args.collection, use_icons=args.icons).run()


if __name__ == "__main__":
    main()
