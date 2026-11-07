"""The hardware contract. These run on a laptop and also check the Pi drivers as soon as they exist."""

import importlib
import importlib.util
import inspect
import time

import pytest

from carebot.config import load_config, load_yaml
from carebot.hw import PARTS, make_hardware
from carebot.hw import base
from carebot.hw.mock import MockButton, MockBuzzer, MockEyes, MockPanTilt

BASES = {"pantilt": base.PanTilt, "eyes": base.Eyes, "buzzer": base.Buzzer, "button": base.Button}


def test_mocks_implement_the_contract():
    hw = make_hardware("mock")
    for name, base_cls in BASES.items():
        part = getattr(hw, name)
        assert isinstance(part, base_cls), name


@pytest.mark.parametrize("name", list(PARTS))
def test_pi_driver_matches_contract_if_present(name):
    """Hardware-owned drivers must import on a laptop (lazy imports) and match base.py exactly."""
    module_name, class_name, _ = PARTS[name]
    if importlib.util.find_spec(module_name) is None:
        pytest.skip(f"{module_name} not written yet")
    module = importlib.import_module(module_name)  # must not need Pi packages
    cls = getattr(module, class_name)
    base_cls = BASES[name]
    assert issubclass(cls, base_cls)
    assert not inspect.isabstract(cls), f"{class_name} leaves abstract methods unimplemented"
    params = list(inspect.signature(cls.__init__).parameters)
    assert params == ["self", "cfg"], f"{class_name}.__init__ must take exactly (self, cfg)"
    for method, _ in inspect.getmembers(base_cls, inspect.isfunction):
        if method.startswith("_"):
            continue
        expected = list(inspect.signature(getattr(base_cls, method)).parameters)
        actual = list(inspect.signature(getattr(cls, method)).parameters)
        assert actual == expected, f"{class_name}.{method}{actual} != contract {expected}"


def test_pi_mode_falls_back_to_mock_when_drivers_cannot_start():
    hw = make_hardware("pi", load_yaml("config/hardware_pi.yaml"))
    assert isinstance(hw.eyes, base.Eyes)
    hw.close()
    hw.close()  # safe twice


def test_pantilt_clamps_and_reports_moving():
    head = MockPanTilt({"pan": {"limit_deg": [-80, 80]}, "tilt": {"limit_deg": [-30, 45]},
                        "settle_ms": 50, "verbose": False})
    head.point(200, -100)
    assert head.position() == (80, -30)
    assert head.is_moving()
    time.sleep(0.07)
    assert not head.is_moving()
    head.point(80, -30)  # same position is not a move
    assert not head.is_moving()


def test_eyes_ignore_unknown_states_without_raising():
    eyes = MockEyes({"verbose": False})
    eyes.set("watching")
    assert eyes.state == "watching"
    eyes.set("rainbow")  # type: ignore[arg-type]
    assert eyes.state == "error"


def test_buzzer_alarm_is_idempotent():
    buzzer = MockBuzzer({"verbose": False})
    buzzer.start_alarm()
    buzzer.start_alarm()
    assert buzzer.alarm_on
    buzzer.stop_alarm()
    buzzer.stop_alarm()
    assert not buzzer.alarm_on


def test_button_calls_latest_callback_with_monotonic_time():
    button = MockButton()
    seen = []
    button.on_press(lambda ts: seen.append(("first", ts)))
    button.on_press(lambda ts: seen.append(("second", ts)))
    button.press()
    assert len(seen) == 1 and seen[0][0] == "second"
    assert abs(seen[0][1] - time.monotonic()) < 1


def test_configs_load():
    assert load_config("config/mock.yaml")["hw"] == {}
    pi = load_config("config/pi.yaml")
    assert pi["hw"]["button"]["gpio"] == 27
