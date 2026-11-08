"""PiPanTilt on a laptop: a fake rpi_hardware_pwm records duty cycles instead of driving pins.
The one real-hardware test at the bottom only runs on the robot (`python -m pytest -m pi`)."""

import math
import sys
import time
import types

import pytest

from carebot.config import load_yaml
from carebot.hw.pantilt_pi import Axis, PiPanTilt

HITEC = {"channel": 0, "min_us": 900, "max_us": 2100, "center_us": 1500, "us_per_deg": 10.0,
         "center_trim_deg": 0, "limit_deg": [-60, 60], "invert": False}


def us(duty_percent, hz=50):
    return duty_percent / 100 * 1e6 / hz


class FakePWM:
    instances = []

    def __init__(self, pwm_channel, hz, chip=0):
        self.channel, self.hz, self.chip = pwm_channel, hz, chip
        self.duty, self.running, self.writes = None, False, 0
        FakePWM.instances.append(self)

    def start(self, duty):
        self.duty, self.running = duty, True

    def change_duty_cycle(self, duty):
        self.duty, self.writes = duty, self.writes + 1

    def stop(self):
        self.running = False


@pytest.fixture
def fake_pwm(monkeypatch):
    FakePWM.instances = []
    monkeypatch.setitem(sys.modules, "rpi_hardware_pwm", types.SimpleNamespace(HardwarePWM=FakePWM))
    return FakePWM


def head_cfg(**over):
    cfg = {"pwm_chip": 0, "pan": dict(HITEC), "tilt": {**HITEC, "channel": 1, "limit_deg": [-30, 45]},
           "home_deg": [0, 0], "settle_ms": 50, "hz": 50}
    cfg.update(over)
    return cfg


# Axis math


def test_center_and_slope():
    axis = Axis(HITEC)
    assert axis.pulse_us(0) == 1500
    assert axis.pulse_us(30) == 1800
    assert axis.pulse_us(-30) == 1200


def test_invert_flips_direction():
    axis = Axis({**HITEC, "invert": True})
    assert axis.pulse_us(30) == 1200


def test_trim_shifts_center():
    assert Axis({**HITEC, "center_trim_deg": 2}).pulse_us(0) == 1520


def test_limits_clamp_and_pulses_stay_in_range():
    axis = Axis({**HITEC, "limit_deg": [-80, 80]})  # 900..2100 us only reaches +-60 deg at 10 us/deg
    assert (axis.lo, axis.hi) == (-60, 60)
    for deg in (-1000, -61, 0, 61, 1000):
        assert 900 <= axis.pulse_us(deg) <= 2100


def test_sg90_defaults_span_180_degrees():
    axis = Axis({"channel": 0, "min_us": 500, "max_us": 2500, "limit_deg": [-80, 80]})
    assert axis.pulse_us(0) == 1500
    assert math.isclose(axis.pulse_us(90 * 0.5), 2000)  # 45 deg = 500 us at 2000/180 us per deg


def test_bad_config_fails_at_start():
    with pytest.raises(ValueError):
        Axis({**HITEC, "min_us": 2100, "max_us": 900})
    with pytest.raises(ValueError):
        Axis({**HITEC, "limit_deg": [70, 80]})  # unreachable with 900..2100 us


# The driver against the contract


def test_starts_at_home_on_the_right_channels(fake_pwm):
    head = PiPanTilt(head_cfg())
    pan, tilt = fake_pwm.instances
    assert (pan.channel, tilt.channel, pan.hz) == (0, 1, 50)
    assert pan.running and tilt.running
    assert math.isclose(us(pan.duty), 1500) and math.isclose(us(tilt.duty), 1500)
    assert head.position() == (0, 0)
    head.close()


def test_point_clamps_writes_only_changed_axes_and_reports_moving(fake_pwm):
    head = PiPanTilt(head_cfg())
    pan, tilt = fake_pwm.instances
    time.sleep(0.07)
    assert not head.is_moving()

    head.point(200, -100)
    assert head.position() == (60, -30)
    assert math.isclose(us(pan.duty), 2100) and math.isclose(us(tilt.duty), 1200)
    assert head.is_moving()

    writes = (pan.writes, tilt.writes)
    head.point(60, 10)  # only tilt changes
    assert (pan.writes, tilt.writes) == (writes[0], writes[1] + 1)

    time.sleep(0.07)
    head.point(60, 10)  # same position is not a move
    assert not head.is_moving()
    head.close()


def test_point_ignores_garbage_without_raising(fake_pwm):
    head = PiPanTilt(head_cfg())
    head.point(float("nan"), 0)
    head.point("left", 0)  # type: ignore[arg-type]
    assert head.position() == (0, 0)
    head.close()


def test_close_stops_pwm_is_safe_twice_and_later_calls_are_ignored(fake_pwm):
    head = PiPanTilt(head_cfg())
    head.close()
    head.close()
    assert not any(p.running for p in fake_pwm.instances)
    head.point(10, 10)  # no raise after close
    head.home()


def test_home_uses_config(fake_pwm):
    head = PiPanTilt(head_cfg(home_deg=[10, 5]))
    head.point(-20, 0)
    head.home()
    assert head.position() == (10, 5)
    head.close()


def test_start_failure_is_loud_and_releases_the_first_channel(monkeypatch):
    started = []

    class Breaks(FakePWM):
        def start(self, duty):
            if self.channel == 1:
                raise OSError("no pwm channel 1")
            super().start(duty)
            started.append(self)

    monkeypatch.setitem(sys.modules, "rpi_hardware_pwm", types.SimpleNamespace(HardwarePWM=Breaks))
    with pytest.raises(RuntimeError, match="pwm-2chan"):
        PiPanTilt(head_cfg())
    assert started and not started[0].running


def test_committed_config_builds_a_valid_head(fake_pwm):
    cfg = load_yaml("config/hardware_pi.yaml")["pantilt"]
    head = PiPanTilt(cfg)
    assert head.position() == tuple(cfg["home_deg"])
    head.close()


# On the robot only


@pytest.mark.pi
def test_real_head_moves_and_stops():
    head = PiPanTilt(load_yaml("config/hardware_pi.yaml")["pantilt"])
    try:
        head.point(10, 5)
        assert head.is_moving()
        assert head.position() == (10, 5)
        head.home()
    finally:
        head.close()
        head.close()
