"""`ankitui server`: run Anki's self-hosted sync server.

The server is the one from the official ``anki`` package (the same code AnkiWeb
is built on), configured through its SYNC_* environment variables. Anki desktop,
AnkiDroid, AnkiMobile and AnkiTUI can all sync with it.
"""

from __future__ import annotations

import argparse
import getpass
import os
import socket
import sys


def default_base() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "ankitui", "syncserver")


def lan_addresses() -> list[str]:
    """Best-effort list of this machine's IPv4 addresses that other devices can reach."""
    found: list[str] = []
    try:
        # no packet is sent; this only asks the OS which interface would be used
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr not in found and not addr.startswith("127."):
                found.append(addr)
    except OSError:
        pass
    return found


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ankitui server",
        description="Run a self-hosted Anki sync server (needs the anki package: pip install 'ankitui[ankiweb]').",
        epilog="Users can also be given in the environment as SYNC_USER1=name:password, SYNC_USER2=..., "
        "like Anki's own `python -m anki.syncserver`.",
    )
    parser.add_argument(
        "-u", "--user", action="append", default=[], metavar="NAME[:PASSWORD]",
        help="an account that may sync (repeat for more). Without a password you are asked for one.",
    )
    parser.add_argument("--host", default=os.environ.get("SYNC_HOST", "0.0.0.0"), help="address to listen on (default: all interfaces)")
    parser.add_argument("-p", "--port", type=int, default=int(os.environ.get("SYNC_PORT", 8080)), help="port (default: 8080)")
    parser.add_argument("--data", default=os.environ.get("SYNC_BASE") or default_base(), help="where the server keeps collections and media")
    parser.add_argument("--hashed", action="store_true", help="passwords are PHC-format hashes (Anki's PASSWORDS_HASHED=1)")
    parser.add_argument("--max-payload", type=int, metavar="MB", help="largest upload accepted, in MB (default 100)")
    return parser.parse_args(argv)


def server_env(args: argparse.Namespace, environ: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in environ.items() if not k.startswith("SYNC_USER")} if args.user else dict(environ)
    for i, user in enumerate(args.user, start=1):
        name, sep, password = user.partition(":")
        if not name:
            raise SystemExit(f"ankitui server: invalid user {user!r}")
        if not sep:
            password = getpass.getpass(f"Password for {name}: ")
            if not password:
                raise SystemExit("ankitui server: empty password")
        env[f"SYNC_USER{i}"] = f"{name}:{password}"
    if not any(k.startswith("SYNC_USER") for k in env):
        raise SystemExit("ankitui server: add at least one account, e.g. `ankitui server -u alice` (asks for a password)")
    env["SYNC_HOST"] = args.host
    env["SYNC_PORT"] = str(args.port)
    env["SYNC_BASE"] = os.path.expanduser(args.data)
    if args.hashed:
        env["PASSWORDS_HASHED"] = "1"
    if args.max_payload:
        env["MAX_SYNC_PAYLOAD_MEGS"] = str(args.max_payload)
    return env


def main(argv: list[str]) -> None:
    args = parse_args(argv)
    try:
        from anki.syncserver import run_sync_server
    except ImportError:
        raise SystemExit(
            "ankitui server needs the official anki package.\n"
            "Install it with: pip install 'ankitui[ankiweb]'  (or: uv sync --extra ankiweb)"
        )
    env = server_env(args, dict(os.environ))
    os.makedirs(env["SYNC_BASE"], exist_ok=True)
    os.environ.clear()
    os.environ.update(env)

    users = sorted(v.partition(":")[0] for k, v in env.items() if k.startswith("SYNC_USER"))
    hosts = lan_addresses() if args.host in ("0.0.0.0", "::") else [args.host]
    print("Anki sync server")
    print(f"  accounts: {', '.join(users)}")
    print(f"  data:     {env['SYNC_BASE']}")
    print("  In AnkiDroid / Anki / AnkiTUI, set the sync server to:")
    for host in hosts or ["<this computer's IP>"]:
        print(f"    http://{host}:{args.port}/")
    print("  (on the same machine: http://127.0.0.1:%d/)  Press Ctrl+C to stop.\n" % args.port)
    sys.stdout.flush()
    try:
        run_sync_server()
    except KeyboardInterrupt:
        pass
