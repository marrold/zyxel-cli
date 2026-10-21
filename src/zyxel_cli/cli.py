from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import sys

from .client import AuthenticationError, PortConfig, ZyxelClient, ZyxelError
from .config import ConfigError, SwitchConfig, load_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zyxelcli",
        description="Control Ethernet and PoE together on a Zyxel GS1200-5HP v2",
    )
    parser.add_argument("--config", type=Path, default=Path.home() / ".zyxel-cli")
    parser.add_argument("--switch", "-s", dest="switch_name", help="named switch from config")
    parser.add_argument("--host", help="switch host or URL (overrides config)")
    parser.add_argument("--password", help="password (prefer config, ZYXEL_PASSWORD, or prompt)")
    parser.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout in seconds")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="show port and PoE administrative state")
    for command in ("enable", "disable"):
        child = subparsers.add_parser(command, help=f"{command} Ethernet and PoE together")
        child.add_argument("ports", nargs="+", help="one or more port names or numbers")
        child.add_argument("--dry-run", action="store_true", help="show masks without writing")
    run = subparsers.add_parser("run", help="run a configured macro")
    run.add_argument("action", help="macro name")
    run.add_argument("--dry-run", action="store_true", help="show masks without writing")
    return parser


def _normalise_macro_invocation(argv: list[str]) -> list[str]:
    """Allow `zyxelcli "enable RFP1"` as shorthand for the run subcommand."""
    commands = {"status", "enable", "disable", "run", "-h", "--help"}
    if not any(argument in commands for argument in argv):
        positional = [index for index, argument in enumerate(argv) if not argument.startswith("-")]
        if positional:
            index = positional[-1]
            if " " in argv[index]:
                return argv[:index] + ["run", argv[index]] + argv[index + 1 :]
    return argv


def _password(args: argparse.Namespace, config: SwitchConfig) -> str:
    password = args.password or os.environ.get("ZYXEL_PASSWORD") or config.password
    if password is None:
        password = getpass.getpass("Switch password: ")
    if not password:
        raise ValueError("password cannot be empty")
    return password


def _state(config: PortConfig, port: int) -> tuple[str, str]:
    bit = 1 << (port - 1)
    return (
        "enabled" if config.state_mask & bit else "disabled",
        "enabled" if config.poe_mask & bit else "disabled",
    )


def _print_status(state: PortConfig, config: SwitchConfig) -> None:
    print("PORT  NAME              ETHERNET  POE")
    for port in range(1, 6):
        ethernet, poe = _state(state, port)
        poe = poe if port <= 4 else "n/a"
        print(f"{port:>4}  {(config.port_name(port) or '-'):<16}  {ethernet:<8}  {poe}")


def _show_change(before: PortConfig, after: PortConfig, *, dry_run: bool) -> None:
    print("Proposed changes:" if dry_run else "Applied changes:")
    print(f"port mask: {before.state_mask} -> {after.state_mask}")
    print(f"PoE mask:  {before.poe_mask} -> {after.poe_mask}")


def main(argv: list[str] | None = None) -> int:
    argv = _normalise_macro_invocation(list(sys.argv[1:] if argv is None else argv))
    args = _parser().parse_args(argv)
    try:
        catalog = load_config(args.config)
        switch_name, config = catalog.select(args.switch_name)
        host = args.host or config.host or "192.168.1.3"
        password = _password(args, config)
        if switch_name:
            print(f"Switch: {switch_name}")
        with ZyxelClient(host, password, timeout=args.timeout) as client:
            if args.command == "status":
                _print_status(client.get_port_config(), config)
                return 0

            if args.command == "run":
                macro = config.macro(args.action)
                enable = [config.resolve_port(port) for port in macro.enable]
                disable = [config.resolve_port(port) for port in macro.disable]
            else:
                ports = [config.resolve_port(port) for port in args.ports]
                enable = ports if args.command == "enable" else []
                disable = ports if args.command == "disable" else []

            before, after = client.set_port_states(
                enable=enable, disable=disable, dry_run=args.dry_run
            )
            _show_change(before, after, dry_run=args.dry_run)
            return 0
    except (AuthenticationError, ConfigError, ZyxelError, ValueError) as error:
        print(f"zyxelcli: error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("zyxelcli: interrupted", file=sys.stderr)
        return 130
