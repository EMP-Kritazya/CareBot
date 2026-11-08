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


def load_env(path: str | Path = ".env") -> None:
    """Read KEY=VALUE lines from .env (git-ignored) into the environment, without overriding
    variables that are already set. Secrets live only there, never in config files."""
    import os

    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #")[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def apply_stillness_flags(cfg: dict, demo: bool = False, override_s: float | None = None) -> None:
    """--demo switches every timer to its demo value (stillness, cooldown, fall hold, answer windows,
    comfort windows); --stillness N sets every stillness timer of the active profile to N seconds."""
    st = cfg["detectors"]["stillness"]
    if demo:
        st["profile"] = "demo"
        fall = cfg["detectors"]["fall"]
        fall["hold_s"] = fall.get("hold_s_demo", fall["hold_s"])
        check_in = cfg.setdefault("check_in", {})
        check_in["listen_s"] = check_in.get("listen_s_demo", check_in.get("listen_s", 15))
        comfort = cfg.get("ai", {}).get("comfort", {})
        for key in ("every_s", "listen_s"):
            if f"{key}_demo" in comfort:
                comfort[key] = comfort[f"{key}_demo"]
    if override_s:
        profile = st.get("profile", "real")
        st["timers"][profile] = {kind: float(override_s) for kind in st["timers"][profile]}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
