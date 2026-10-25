"""Command line entry point: `ankitui [--collection PATH]`."""

from __future__ import annotations

import argparse

from . import __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ankitui", description="A terminal client for Anki collections.")
    parser.add_argument(
        "-c",
        "--collection",
        help="path to the collection file (default: $ANKITUI_COLLECTION or ~/.local/share/ankitui/collection.anki2)",
    )
    parser.add_argument("--version", action="version", version=f"ankitui {__version__}")
    args = parser.parse_args(argv)

    from .ui.app import AnkiTUI

    AnkiTUI(args.collection).run()


if __name__ == "__main__":
    main()
