import random

import pytest

from zyxel_cli.client import PortConfig, ProtocolError, encode_password


SAMPLE = """
var portState = '31';
var portPoE = '13';
var portFlctl = '6';
var portSpeed = ['0', '1', '2', '3', '0'];
"""


def test_password_encoding_shape_and_shift():
    password = "gateway1"
    encoded = encode_password(password, rng=random.Random(1))
    assert len(encoded) == len(password) * 2 + 1
    assert "".join(encoded[index] for index in range(1, len(encoded) - 1, 2)) == "_Yl]oYq)"


def test_parse_and_form_preserve_all_fields():
    config = PortConfig.from_javascript(SAMPLE)
    assert config == PortConfig(31, 13, 6, (0, 1, 2, 3, 0))
    assert config.form_data() == {
        "g_port_state": 31,
        "g_port_poe": 13,
        "g_port_flwcl": 6,
        "g_port_speed0": 0,
        "g_port_speed1": 1,
        "g_port_speed2": 2,
        "g_port_speed3": 3,
        "g_port_speed4": 0,
    }


def test_disable_and_enable_change_both_masks():
    config = PortConfig.from_javascript(SAMPLE)
    disabled = config.with_ports([1, 3], False)
    assert disabled.state_mask == 26
    assert disabled.poe_mask == 8
    assert disabled.with_ports([1, 3], True).state_mask == 31
    assert disabled.with_ports([1, 3], True).poe_mask == 13


def test_mixed_changes_form_one_atomic_target_state():
    config = PortConfig(26, 8, 6, (0, 1, 2, 3, 0))
    changed = config.with_port_changes(enable=[1], disable=[4])
    assert changed == PortConfig(19, 1, 6, (0, 1, 2, 3, 0))


def test_refuses_incomplete_state_to_avoid_overwriting_settings():
    with pytest.raises(ProtocolError, match="flow control, port speeds"):
        PortConfig.from_javascript("var portState='31'; var portPoE='15';")
