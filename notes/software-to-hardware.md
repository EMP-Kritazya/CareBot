# Notes: Software → Hardware

Only the software side (Kritazya) writes here. Newest at the bottom, under a timestamp heading.

## 2026-11-07, hour 0: the contract is pushed, you can start

- `carebot/hw/base.py` is the interface, exactly as in `docs/HARDWARE.md`.
- `carebot/hw/mock.py` shows the expected behavior of each part.
- `carebot/hw/__init__.py` has `make_hardware("pi", cfg)`. Use it in `apps/hwcheck.py`. If one of
  your drivers fails in `__init__`, only that part falls back to its mock (and the error is logged).
- `config/hardware_pi.yaml` is seeded from the doc's template and is yours now.
- `tests/test_hw_contract.py` imports each `*_pi.py` on a laptop and checks it against `base.py`
  (subclass, every method implemented, same signatures, `__init__(self, cfg)`). Run
  `python -m pytest` on a laptop before each push; if that test fails, the contract is broken.
- Setup: `uv venv --python 3.11 .venv && uv pip install --python .venv -r requirements.txt`
  (on the Pi: `python3.11 -m venv --system-site-packages .venv`, then pip install both requirements files).
