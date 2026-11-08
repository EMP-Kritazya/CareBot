"""PiButton, PiBuzzer and PiEyes on a laptop: fake gpiozero / Blinka modules stand in for the pins.
Real-hardware tests at the bottom run only on the robot (`python -m pytest -m pi`)."""

import sys
import time
import types

import pytest

from carebot.hw.base import EYE_STATES
from carebot.hw.eyes_pi import AMBER, BLUE, GREEN, OFF, RED, render


# Fake gpiozero


class FakeButton:
    def __init__(self, pin, pull_up=True, bounce_time=None):
        self.pin, self.pull_up, self.bounce_time = pin, pull_up, bounce_time
        self.when_pressed, self.closed = None, False

    def press(self):
        if self.when_pressed:
            self.when_pressed()

    def close(self):
        self.closed = True


class FakeOut:
    def __init__(self, pin, frequency=None, initial_value=0):
        self.pin, self.frequency, self.value, self.closed, self.history = pin, frequency, initial_value, False, []

    def on(self):
        self.value = True
        self.history.append(True)

    def off(self):
        self.value = False
        self.history.append(False)

    def close(self):
        self.closed = True


@pytest.fixture
def gpio(monkeypatch):
    made = []

    def track(cls):
        def make(*a, **k):
            obj = cls(*a, **k)
            made.append(obj)
            return obj
        return make

    fake = types.SimpleNamespace(Button=track(FakeButton), DigitalOutputDevice=track(FakeOut),
                                 PWMOutputDevice=track(FakeOut))
    monkeypatch.setitem(sys.modules, "gpiozero", fake)
    return made


# Button


def test_button_pin_pull_and_latest_callback(gpio):
    from carebot.hw.button_pi import PiButton

    button = PiButton({"gpio": 27, "pull_up": False, "bounce_ms": 50})
    pin = gpio[0]
    assert (pin.pin, pin.pull_up) == (27, False)
    seen = []
    button.on_press(lambda ts: seen.append(("first", ts)))
    button.on_press(lambda ts: seen.append(("second", ts)))
    pin.press()
    assert len(seen) == 1 and seen[0][0] == "second"
    assert abs(seen[0][1] - time.monotonic()) < 1


def test_button_debounces_and_survives_a_bad_callback(gpio):
    from carebot.hw.button_pi import PiButton

    button = PiButton({"gpio": 27, "bounce_ms": 50})
    pin = gpio[0]
    calls = []
    button.on_press(lambda ts: calls.append(ts))
    pin.press()
    pin.press()  # bounce: within 50 ms
    assert len(calls) == 1
    time.sleep(0.06)
    button.on_press(lambda ts: 1 / 0)
    pin.press()  # must not raise into gpiozero's thread


def test_button_close_is_safe_twice_and_silences(gpio):
    from carebot.hw.button_pi import PiButton

    button = PiButton({"gpio": 27})
    calls = []
    button.on_press(calls.append)
    button.close()
    button.close()
    gpio[0].press()
    assert gpio[0].closed and calls == []


# Buzzer


def test_active_chirp_is_short(gpio):
    from carebot.hw.buzzer_pi import CHIRP_S, PiBuzzer

    buzzer = PiBuzzer({"gpio": 17, "type": "active"})
    pin = gpio[0]
    buzzer.chirp()
    assert pin.value is True
    time.sleep(CHIRP_S + 0.08)
    assert pin.value is False
    buzzer.close()


def test_passive_uses_a_tone(gpio):
    from carebot.hw.buzzer_pi import PiBuzzer

    buzzer = PiBuzzer({"gpio": 17, "type": "passive", "passive_hz": 2800})
    pin = gpio[0]
    assert pin.frequency == 2800
    buzzer.chirp()
    assert pin.value == 0.5
    buzzer.close()
    assert pin.value == 0 and pin.closed


def test_alarm_repeats_is_idempotent_and_stops_silent(gpio):
    from carebot.hw.buzzer_pi import PiBuzzer

    buzzer = PiBuzzer({"gpio": 17, "type": "active", "alarm_on_ms": 30, "alarm_off_ms": 20})
    pin = gpio[0]
    buzzer.start_alarm()
    buzzer.start_alarm()
    buzzer.chirp()  # ignored while the alarm sounds
    time.sleep(0.18)
    assert pin.history.count(True) >= 3  # several beeps
    buzzer.stop_alarm()
    buzzer.stop_alarm()
    assert pin.value is False
    time.sleep(0.08)
    assert pin.value is False  # nothing turned it back on
    buzzer.close()
    buzzer.close()


# Eyes: the looks from docs/HARDWARE.md


def test_every_state_renders_the_right_size():
    for state in EYE_STATES:
        for t in (0, 0.3, 1.7):
            frame = render(state, t, 12)
            assert len(frame) == 12
            assert all(0 <= v <= 255 for c in frame for v in c)


def test_looks():
    assert render("off", 1, 12) == [OFF] * 12
    assert render("help_coming", 5, 12) == [GREEN] * 12
    assert render("watching", 2.0, 12)[0] == BLUE  # breathing peaks halfway through 4 s
    assert sum(render("watching", 0, 12)[0]) < sum(BLUE) / 3
    assert render("checking", 0.5, 12)[0] == AMBER
    assert render("alerted", 0.1, 12)[0] == RED and render("alerted", 0.35, 12)[0] == OFF
    boot = render("boot", 0, 12)
    assert boot[0] == (255, 255, 255) and sum(c != OFF for c in boot) == 3


def test_brightness_cap_and_unknown_state():
    assert render("help_coming", 0, 4, max_brightness=0.3) == [(0, 76, 12)] * 4
    assert render("rainbow", 0, 3) == render("error", 0, 3)


def test_eyes_driver_with_fake_neopixels(monkeypatch):
    shown = []

    class FakePixels(list):
        def __init__(self, spi, n, pixel_order=None, auto_write=False, brightness=1.0):
            super().__init__([OFF] * n)

        def show(self):
            shown.append(list(self))

    spi = types.SimpleNamespace(deinit=lambda: None)
    monkeypatch.setitem(sys.modules, "board", types.SimpleNamespace(SPI=lambda: spi))
    monkeypatch.setitem(sys.modules, "neopixel_spi", types.SimpleNamespace(NeoPixel_SPI=FakePixels, GRB="GRB"))
    from carebot.hw.eyes_pi import PiEyes

    eyes = PiEyes({"num_pixels": 6, "max_brightness": 1.0, "pixel_order": "GRB"})
    eyes.set("help_coming")
    eyes.set("help_coming")  # no-op
    time.sleep(0.15)
    assert shown[-1] == [GREEN] * 6
    eyes.set("rainbow")  # type: ignore[arg-type]  # never raises
    eyes.close()
    eyes.close()
    assert shown[-1] == [OFF] * 6


# On the robot only


@pytest.mark.pi
def test_real_parts_start_and_close():
    from carebot.config import load_yaml
    from carebot.hw.button_pi import PiButton
    from carebot.hw.buzzer_pi import PiBuzzer
    from carebot.hw.eyes_pi import PiEyes

    cfg = load_yaml("config/hardware_pi.yaml")
    for cls, part in ((PiButton, "button"), (PiBuzzer, "buzzer"), (PiEyes, "eyes")):
        driver = cls(cfg[part])
        driver.close()
        driver.close()
