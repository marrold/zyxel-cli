from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """The YAML configuration is invalid."""


@dataclass(frozen=True)
class Macro:
    enable: tuple[str | int, ...] = ()
    disable: tuple[str | int, ...] = ()


@dataclass(frozen=True)
class SwitchConfig:
    host: str | None = None
    password: str | None = None
    ports: dict[str, int] = field(default_factory=dict)
    macros: dict[str, Macro] = field(default_factory=dict)

    def resolve_port(self, value: str | int) -> int:
        if isinstance(value, int) or str(value).isdigit():
            number = int(value)
            if number < 1 or number > 4:
                raise ConfigError(f"PoE port must be between 1 and 4, got {number}")
            return number
        key = str(value).casefold()
        matches = [number for name, number in self.ports.items() if name.casefold() == key]
        if not matches:
            names = ", ".join(sorted(self.ports)) or "none configured"
            raise ConfigError(f"unknown port {value!r}; configured names: {names}")
        return matches[0]

    def port_name(self, number: int) -> str | None:
        return next((name for name, port in self.ports.items() if port == number), None)

    def macro(self, name: str) -> Macro:
        match = self.find_macro(name)
        if match is None:
            names = ", ".join(sorted(self.macros)) or "none configured"
            raise ConfigError(f"unknown macro {name!r}; configured macros: {names}")
        return match

    def find_macro(self, name: str) -> Macro | None:
        key = name.casefold()
        return next(
            (macro for macro_name, macro in self.macros.items() if macro_name.casefold() == key),
            None,
        )


@dataclass(frozen=True)
class Config:
    default: str | None = None
    switches: dict[str, SwitchConfig] = field(default_factory=dict)

    def select(self, name: str | None) -> tuple[str | None, SwitchConfig]:
        selected = name or self.default
        if selected is None:
            if not self.switches:
                return None, SwitchConfig()
            if len(self.switches) == 1:
                selected = next(iter(self.switches))
            else:
                names = ", ".join(sorted(self.switches))
                raise ConfigError(
                    f"no default switch configured; use --switch with one of: {names}"
                )
        key = selected.casefold()
        match = next(
            ((switch_name, switch) for switch_name, switch in self.switches.items() if switch_name.casefold() == key),
            None,
        )
        if match is None:
            names = ", ".join(sorted(self.switches)) or "none configured"
            raise ConfigError(f"unknown switch {selected!r}; configured switches: {names}")
        return match


def _items(value: Any, label: str) -> tuple[str | int, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, int)):
        return (value,)
    if not isinstance(value, list) or not all(isinstance(item, (str, int)) for item in value):
        raise ConfigError(f"{label} must be a port name/number or a list of them")
    return tuple(value)


def _load_switch(value: Any, label: str) -> SwitchConfig:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a mapping")

    raw_ports = value.get("ports", {})
    if not isinstance(raw_ports, dict):
        raise ConfigError(f"{label} ports must be a mapping of names to port numbers")
    ports: dict[str, int] = {}
    for name, number in raw_ports.items():
        if not isinstance(name, str) or not isinstance(number, int):
            raise ConfigError(f"{label} ports must be a mapping such as RFP1: 1")
        if number < 1 or number > 4:
            raise ConfigError(f"{label} named PoE port {name!r} must be between 1 and 4")
        if number in ports.values():
            raise ConfigError(f"{label} port {number} has more than one name")
        if name.casefold() in {existing.casefold() for existing in ports}:
            raise ConfigError(f"{label} has duplicate port name {name!r}")
        ports[name] = number

    raw_macros = value.get("macros", {})
    if not isinstance(raw_macros, dict):
        raise ConfigError(f"{label} macros must be a mapping")
    macros: dict[str, Macro] = {}
    resolver = SwitchConfig(ports=ports)
    for name, macro_value in raw_macros.items():
        if not isinstance(name, str) or not isinstance(macro_value, dict):
            raise ConfigError(f"{label} macros must each be a named mapping")
        macro = Macro(
            enable=_items(macro_value.get("enable"), f"macro {name!r} enable"),
            disable=_items(macro_value.get("disable"), f"macro {name!r} disable"),
        )
        if not macro.enable and not macro.disable:
            raise ConfigError(f"macro {name!r} has no enable or disable ports")
        enabled = {resolver.resolve_port(item) for item in macro.enable}
        disabled = {resolver.resolve_port(item) for item in macro.disable}
        overlap = enabled & disabled
        if overlap:
            raise ConfigError(f"macro {name!r} both enables and disables port(s) {sorted(overlap)}")
        if name.casefold() in {existing.casefold() for existing in macros}:
            raise ConfigError(f"{label} has duplicate macro name {name!r}")
        macros[name] = macro

    host = value.get("host")
    password = value.get("password")
    if host is not None and not isinstance(host, str):
        raise ConfigError(f"{label} host must be a string")
    if password is not None and not isinstance(password, str):
        raise ConfigError(f"{label} password must be a string")
    return SwitchConfig(host=host, password=password, ports=ports, macros=macros)


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    if document is None:
        return Config()
    if not isinstance(document, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")

    # Backwards compatibility with the original single-switch layout.
    if "switches" not in document:
        return Config(default="default", switches={"default": _load_switch(document, "switch")})

    default = document.get("default")
    if default is not None and not isinstance(default, str):
        raise ConfigError("default must be a switch name")
    raw_switches = document["switches"]
    if not isinstance(raw_switches, dict) or not raw_switches:
        raise ConfigError("switches must be a non-empty mapping")
    switches: dict[str, SwitchConfig] = {}
    for name, value in raw_switches.items():
        if not isinstance(name, str) or not name:
            raise ConfigError("each switch must have a non-empty name")
        if name.casefold() in {existing.casefold() for existing in switches}:
            raise ConfigError(f"duplicate switch name {name!r}")
        switches[name] = _load_switch(value, f"switch {name!r}")
    config = Config(default=default, switches=switches)
    if default is not None:
        config.select(None)  # Validate that the named default exists.
    return config
