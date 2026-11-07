"""Loads a run config (config/mock.yaml or config/pi.yaml) and, on the robot, the hardware config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    with p.open() as f:
        return yaml.safe_load(f) or {}


def load_config(path: str | Path = "config/mock.yaml") -> dict[str, Any]:
    cfg = load_yaml(path)
    cfg.setdefault("hardware", "mock")
    cfg["hw"] = load_yaml(cfg["hardware_config"]) if cfg["hardware"] == "pi" else {}
    cfg["detectors"] = _deep_merge(load_yaml(cfg.get("detectors_config", "config/detectors.yaml")),
                                   cfg.get("detectors") or {})
    return cfg


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
