from pathlib import Path

import pytest

from zyxel_cli.cli import _normalise_macro_invocation
from zyxel_cli.config import ConfigError, load_config


def test_missing_config_is_optional(tmp_path: Path):
    catalog = load_config(tmp_path / "missing")
    name, config = catalog.select(None)
    assert name is None
    assert config.host is None
    assert config.ports == {}


def test_named_ports_and_swap_macros(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """\
host: 192.168.1.3
password: secret
ports:
  RFP1: 1
  RFP2: 2
macros:
  enable RFP1:
    enable: RFP1
    disable: [RFP2]
""",
        encoding="utf-8",
    )
    catalog = load_config(path)
    name, config = catalog.select(None)
    assert name == "default"
    assert config.host == "192.168.1.3"
    assert config.resolve_port("rfp1") == 1
    assert config.macro("ENABLE rfp1").enable == ("RFP1",)
    assert config.macro("enable RFP1").disable == ("RFP2",)


def test_multiple_named_switches_and_default(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """\
default: workshop
switches:
  workshop:
    host: 192.168.1.3
    password: first
    ports: {RFP1: 1, RFP2: 2}
  studio:
    host: 192.168.1.4
    password: second
    ports: {MAIN: 1, BACKUP: 2}
""",
        encoding="utf-8",
    )
    catalog = load_config(path)
    name, switch = catalog.select(None)
    assert name == "workshop"
    assert switch.host == "192.168.1.3"
    name, switch = catalog.select("STUDIO")
    assert name == "studio"
    assert switch.host == "192.168.1.4"


def test_multiple_switches_require_default_or_selection(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "switches: {one: {host: 192.0.2.1}, two: {host: 192.0.2.2}}\n",
        encoding="utf-8",
    )
    catalog = load_config(path)
    with pytest.raises(ConfigError, match="no default switch"):
        catalog.select(None)
    assert catalog.select("two")[1].host == "192.0.2.2"


def test_macro_cannot_enable_and_disable_same_port(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "ports: {RFP1: 1}\nmacros: {bad: {enable: RFP1, disable: 1}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="both enables and disables"):
        load_config(path)


def test_quoted_macro_shorthand():
    assert _normalise_macro_invocation(["enable RFP1"]) == ["run", "enable RFP1"]
    assert _normalise_macro_invocation(["--config", "x", "enable RFP1", "--dry-run"]) == [
        "--config",
        "x",
        "run",
        "enable RFP1",
        "--dry-run",
    ]
