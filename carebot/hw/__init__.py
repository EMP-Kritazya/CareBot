"""Hardware factory: picks the Pi driver or the mock for each part.

Owned by Software. Pi drivers are imported lazily, so laptops never need Pi packages.
If a Pi driver fails to start and `fallback_to_mock` is on, that one part falls back to its mock
and the failure is logged, so the rest of the robot keeps working.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass

from carebot.hw.base import Button, Buzzer, Eyes, PanTilt
from carebot.hw.mock import MockButton, MockBuzzer, MockEyes, MockPanTilt

log = logging.getLogger(__name__)

# part name -> (Pi module, Pi class, mock class)
PARTS = {
    "pantilt": ("carebot.hw.pantilt_pi", "PiPanTilt", MockPanTilt),
    "eyes": ("carebot.hw.eyes_pi", "PiEyes", MockEyes),
    "buzzer": ("carebot.hw.buzzer_pi", "PiBuzzer", MockBuzzer),
    "button": ("carebot.hw.button_pi", "PiButton", MockButton),
}


@dataclass
class Hardware:
    pantilt: PanTilt
    eyes: Eyes
    buzzer: Buzzer
    button: Button

    def close(self) -> None:
        for part in (self.buzzer, self.eyes, self.pantilt, self.button):
            try:
                part.close()
            except Exception:  # noqa: BLE001 - closing must never crash shutdown
                log.exception("closing %s failed", type(part).__name__)


def _make_part(name: str, mode: str, cfg: dict, fallback_to_mock: bool):
    module_name, class_name, mock_cls = PARTS[name]
    part_cfg = cfg.get(name, {})
    if mode != "pi":
        return mock_cls(part_cfg)
    try:
        cls = getattr(importlib.import_module(module_name), class_name)
        return cls(part_cfg)
    except Exception:
        if not fallback_to_mock:
            raise
        log.exception("%s: Pi driver failed to start, using the mock instead", name)
        return mock_cls(part_cfg)


def make_hardware(mode: str = "mock", cfg: dict | None = None, fallback_to_mock: bool = True) -> Hardware:
    """mode: "mock" or "pi". cfg: the parsed config/hardware_pi.yaml (sections per part)."""
    cfg = cfg or {}
    return Hardware(**{name: _make_part(name, mode, cfg, fallback_to_mock) for name in PARTS})
