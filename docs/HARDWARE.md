# CareBot hardware layer: contract and guide

This document is for the teammate who builds the Raspberry Pi hardware layer, and for the Claude they
work with. Read it fully before writing code. It defines exactly what you build, which files are
yours, and the interface the rest of CareBot calls. If your code follows this contract, it plugs
into the app with no integration work.

Also read the root `CLAUDE.md` (project rules) and `PLAN.md` (what the robot does and why).

> **Status: these files don't exist yet.** This document describes the target layout. In the first
> hour, the software side creates `carebot/hw/base.py`, `mock.py`, the factory, an empty
> `config/hardware_pi.yaml` seeded from the template below, and the `notes/` files. **Start coding
> drivers only after those are pushed.** Until then, wire the robot, flash the Pi, and get the
> overlays and Bluetooth working. None of that needs the repo.

## Paste this to your Claude to start

> You are building the Raspberry Pi 5 hardware layer for CareBot. Read `CLAUDE.md`, then
> `docs/HARDWARE.md`, then `carebot/hw/base.py`. Implement only the files listed as owned by
> Hardware in `docs/HARDWARE.md`. Do not edit any other file. If the interface in `base.py` doesn't
> fit, stop and write the reason in `notes/hardware-to-software.md`, don't work around it.
> Start with `apps/hwcheck.py` and one driver at a time, and test each on the Pi before the next.

## What "hardware layer" means here

There is no microcontroller firmware. The robot's brain is a Raspberry Pi 5 running Python. The
hardware layer is:

1. **Drivers.** Small Python classes that turn calls like `eyes.set("alerted")` into GPIO, PWM and
   SPI signals.
2. **Pi setup.** `config.txt` overlays, Bluetooth speaker pairing, the I2S microphone, systemd units,
   and a bootstrap script so a fresh Pi can be rebuilt quickly.
3. **A bench checker.** `apps/hwcheck.py` exercises every part so we can confirm the robot works
   before running the full app.

Drivers are dumb on purpose. They never decide _when_ to move, beep or light up. The software side
(state machine, vision, voice) decides that and calls the driver.

## Who owns which files

| Path                            | Owner        | Notes                                                            |
| ------------------------------- | ------------ | ---------------------------------------------------------------- |
| `carebot/hw/base.py`            | Software     | The interface. Read it; don't edit it.                           |
| `carebot/hw/mock.py`            | Software     | Laptop stand-ins with the same behavior                          |
| `carebot/hw/__init__.py`        | Software     | The factory that picks real or mock drivers                      |
| `carebot/hw/pantilt_pi.py`      | **Hardware** | `PiPanTilt`                                                      |
| `carebot/hw/eyes_pi.py`         | **Hardware** | `PiEyes`                                                         |
| `carebot/hw/buzzer_pi.py`       | **Hardware** | `PiBuzzer`                                                       |
| `carebot/hw/button_pi.py`       | **Hardware** | `PiButton`                                                       |
| `config/hardware_pi.yaml`       | **Hardware** | Pins, calibration, limits, audio device names                    |
| `setup/**`                      | **Hardware** | `bootstrap.sh`, `config.txt` snippet, systemd units, audio notes |
| `apps/hwcheck.py`               | **Hardware** | Bench test for every part                                        |
| `tests/hw/**`                   | **Hardware** | Pi-only tests, marked `@pytest.mark.pi`                          |
| `docs/wiring.md`                | **Hardware** | Final pin map, a wiring photo, power notes                       |
| `requirements-pi.txt`           | **Hardware** | Pi-only packages. Never add them to `requirements.txt`.          |
| `notes/hardware-to-software.md` | **Hardware** | Your messages to the software side                               |
| Everything else                 | Software     | Don't edit. Ask through your notes file.                         |

If you need something changed in a file you don't own, write it in
`notes/hardware-to-software.md` (newest at the bottom, under a timestamp heading) and tell Kritazya.
The software side replies in `notes/software-to-hardware.md`.

## The interface (`carebot/hw/base.py`)

The software side writes this file first. Your classes subclass these and must match the
signatures, units and behavior exactly. This is the contract.

```python
from abc import ABC, abstractmethod
from typing import Callable, Literal

EyeState = Literal["off", "boot", "watching", "checking", "alerted", "help_coming", "error"]


class PanTilt(ABC):
    """Two-servo camera head. Angles in degrees. (0, 0) = looking straight ahead, level."""

    @abstractmethod
    def point(self, pan_deg: float, tilt_deg: float) -> None:
        """Command an absolute position. Returns immediately (non-blocking).
        pan_deg > 0 turns the camera toward the right side of its image.
        tilt_deg > 0 tilts the camera up.
        Values outside the configured limits are clamped, never raised."""

    @abstractmethod
    def home(self) -> None:
        """Go to the configured home pose. Non-blocking."""

    @abstractmethod
    def position(self) -> tuple[float, float]:
        """Last commanded (pan_deg, tilt_deg), after clamping."""

    @abstractmethod
    def is_moving(self) -> bool:
        """True until `settle_ms` has passed since the last command that changed the position.
        SG90s have no feedback, so this is time-based. Vision skips frames while it is True."""

    @abstractmethod
    def close(self) -> None:
        """Stop PWM output and release the pins. Safe to call twice."""


class Eyes(ABC):
    """The LED arc. Callers set a meaning, never a color."""

    @abstractmethod
    def set(self, state: EyeState) -> None:
        """Switch to the state's look (see the table in docs/HARDWARE.md). Non-blocking.
        Animations run on the driver's own thread. Setting the current state again is a no-op."""

    @abstractmethod
    def close(self) -> None:
        """Turn all LEDs off and stop the animation thread. Safe to call twice."""


class Buzzer(ABC):
    @abstractmethod
    def chirp(self) -> None:
        """One short beep (about 120 ms) to get attention before a spoken check-in. Non-blocking."""

    @abstractmethod
    def start_alarm(self) -> None:
        """Start the repeating alarm pattern until stop_alarm(). Non-blocking, idempotent."""

    @abstractmethod
    def stop_alarm(self) -> None:
        """Stop the alarm immediately. Idempotent."""

    @abstractmethod
    def close(self) -> None:
        """Silence and release the pin. Safe to call twice."""


class Button(ABC):
    @abstractmethod
    def on_press(self, callback: Callable[[float], None]) -> None:
        """Register the one callback for presses. It receives time.monotonic() at the press.
        Debounced in the driver (one physical press = one call). The callback runs on the
        driver's thread; the caller is responsible for thread safety. A new call replaces
        the previous callback."""

    @abstractmethod
    def close(self) -> None:
        """Release the pin. Safe to call twice."""
```

### Rules every driver follows

1. **Constructor:** `__init__(self, cfg: dict)` receives that part's section of
   `config/hardware_pi.yaml` (for example `cfg = yaml["pantilt"]`). There are no other arguments.
2. **Lazy imports.** Import `gpiozero`, `rpi_hardware_pwm`, `board`, `neopixel_spi` and the like
   _inside_ `__init__`, not at the top of the file. The laptop must be able to import every module
   in the repo without those packages installed.
3. **Never block.** No public method takes more than about 5 ms. Patterns, animations and alarms
   run on daemon threads.
4. **Thread-safe.** Any method may be called from any thread. Use a lock around shared state.
5. **Fail loudly at start, quietly at runtime.** If the hardware is missing or misconfigured,
   raise in `__init__` with a clear message (the factory then falls back to the mock and logs it).
   After a successful start, never raise from a public method during a demo: log the error and
   carry on.
6. **No app logic.** Drivers never touch the network, MQTT, Twilio, the camera, files outside their
   config, or the state machine. They never decide when to beep, track or light up.
7. **`close()` leaves the robot safe:** LEDs off, buzzer silent, servo PWM stopped.
8. **Match the mock.** `carebot/hw/mock.py` shows the expected behavior. If your driver and the
   mock disagree, the contract wins. Tell the software side so the mock gets fixed.

### Eye states

Brightness is capped by `eyes.max_brightness` in the config (0.3 is the default, to protect the power
supply and keep the robot friendly at night).

| State         | Look                                                 | Used when                                   |
| ------------- | ---------------------------------------------------- | ------------------------------------------- |
| `off`         | All off                                              | Shutdown                                    |
| `boot`        | White chase around the arc                           | Starting up                                 |
| `watching`    | Soft blue, a slow "breathing" fade (about 4 s cycle) | Normal monitoring                           |
| `checking`    | Amber, a steady pulse (about 1 s cycle)              | Asking "Are you OK?"                        |
| `alerted`     | Red, a fast flash (about 2 per second)               | Help is being called                        |
| `help_coming` | Steady green                                         | A neighbor said YES, or someone is with her |
| `error`       | Dim purple, steady                                   | A part failed and the robot is degraded     |

### What the app does with your drivers (for context only)

| Robot state                 | Eyes          | Buzzer                    | Head                                          |
| --------------------------- | ------------- | ------------------------- | --------------------------------------------- |
| Monitoring                  | `watching`    | silent                    | follows Ruth; `home()` when nobody is in view |
| Possible fall               | unchanged     | silent                    | stops sending `point()` (frozen)              |
| Check-in                    | `checking`    | `chirp()` before each ask | frozen                                        |
| Alerted                     | `alerted`     | `start_alarm()`           | frozen                                        |
| Button pressed              | unchanged     | `stop_alarm()`            | frozen                                        |
| Neighbor coming or with her | `help_coming` | silent                    | frozen                                        |

The app calls `point()` about 10 to 15 times a second with small steps while following. Moving
smoothly between steps is the caller's job; the driver just goes where it's told.

## Configuration (`config/hardware_pi.yaml`, yours)

Start from this. Every number the drivers use lives here, and none are hard-coded.

```yaml
pantilt:
  pwm_chip: 0 # check /sys/class/pwm (Pi 5: often 0 on kernel 6.6+, 2 on older)
  pan:
    {
      channel: 0,
      gpio: 12,
      min_us: 500,
      max_us: 2500,
      center_trim_deg: 0,
      limit_deg: [-80, 80],
      invert: false,
    }
  tilt:
    {
      channel: 1,
      gpio: 13,
      min_us: 500,
      max_us: 2500,
      center_trim_deg: 0,
      limit_deg: [-30, 45],
      invert: false,
    }
  home_deg: [0, 0]
  settle_ms: 250
  hz: 50

eyes:
  num_pixels: 12
  spi_bus: 0 # SPI0 MOSI = GPIO 10
  pixel_order: GRB
  max_brightness: 0.3

buzzer:
  gpio: 17
  type: active # active | passive
  passive_hz: 2800 # only used when type is passive
  alarm_on_ms: 400
  alarm_off_ms: 300

button:
  gpio: 27
  pull_up: true # button wired between GPIO 27 and GND
  bounce_ms: 50

camera:
  index: 0 # /dev/video0
  rotate: 0 # 0 | 90 | 180 | 270, if the head mounts the webcam rotated

audio:
  output: "bluez_output.XX_XX_XX_XX_XX_XX.1" # PipeWire sink name of the JBL, from `wpctl status`
  output_fallback: "default"
  input: "hw:CARD=...,DEV=0" # INMP441 from `arecord -l`; webcam mic until it arrives
  input_rate: 16000
```

Camera and audio aren't drivers. The software side uses OpenCV, Piper and Vosk directly. Your job
there is to make the devices work at the OS level and put the right names in this file.

## Wiring (target pin map)

Confirm it on the real board and record the final version, with a photo, in `docs/wiring.md`.

| Part               | GPIO                | Physical pin | Notes                                                     |
| ------------------ | ------------------- | ------------ | --------------------------------------------------------- |
| Pan servo signal   | GPIO 12 (PWM0 ch0)  | 32           | Hardware PWM                                              |
| Tilt servo signal  | GPIO 13 (PWM0 ch1)  | 33           | Hardware PWM                                              |
| NeoPixel data      | GPIO 10 (SPI0 MOSI) | 19           | Through a 74AHCT125 to 5 V logic; 330 Ω in series on data |
| Buzzer             | GPIO 17             | 11           | Through a transistor if it draws more than about 15 mA    |
| Button             | GPIO 27             | 13           | Other leg to GND (internal pull-up)                       |
| INMP441 SCK (BCLK) | GPIO 18             | 12           | I2S                                                       |
| INMP441 WS (LRCLK) | GPIO 19             | 35           | I2S                                                       |
| INMP441 SD (data)  | GPIO 20             | 38           | I2S data in                                               |
| INMP441 L/R        | GND                 |              | Selects the left channel                                  |
| INMP441 VDD        | 3.3 V               | 1            | Never 5 V                                                 |

**Power.**

- The servos and the NeoPixels run from a separate 5 V supply (at least 3 A), never from the Pi's
  5 V pin. Its ground is tied to the Pi's ground.
- Put a 1000 µF capacitor across the NeoPixel supply.
- The Pi 5 has its own 27 W USB-C supply.

## Pi 5 gotchas (save yourself an hour)

- **`pigpio` and `rpi_ws281x` do not work on the Pi 5.**
  - Servos: `rpi-hardware-pwm`.
  - LEDs: `adafruit-circuitpython-neopixel-spi`.
  - Buttons and buzzer: `gpiozero`, which uses `lgpio` on Bookworm.
- **`config.txt` is at `/boot/firmware/config.txt`** on Bookworm. Target lines:
  ```
  dtparam=spi=on
  dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
  dtparam=i2s=on
  dtoverlay=adau7002-simple     # INMP441; if it doesn't show up in `arecord -l`, try googlevoicehat-soundcard
  ```
- **The PWM chip number moves between kernels.** After a reboot, check `ls /sys/class/pwm` and set
  `pantilt.pwm_chip`.
- **SG90 calibration.** Find the real `min_us` and `max_us` for each servo by slowly widening the
  range in `hwcheck` until it stops moving or buzzes, then back off 50 µs. Cheap servos differ.
- **Bluetooth audio, headless.** Pair once with `bluetoothctl` (`pair`, `trust`, `connect`). Then
  add a systemd user unit in `setup/` that reconnects the JBL at boot and retries every 10 s. The
  JBL powers itself off when idle, so note in `docs/wiring.md` how to disable that.
  Fallback: AUX into a USB sound card.
- **Python venv:** `python3.11 -m venv --system-site-packages .venv`.

## The bench checker (`apps/hwcheck.py`, yours)

It runs on the Pi with no network. Each check prints PASS or FAIL and what to look for.

```bash
python -m apps.hwcheck --all
python -m apps.hwcheck pantilt   # sweep pan -80..80, tilt -30..45, return home; then interactive arrow-key jog
python -m apps.hwcheck eyes      # cycle every EyeState for 3 s each
python -m apps.hwcheck buzzer    # chirp, then alarm for 3 s, then stop
python -m apps.hwcheck button    # print each press with its timestamp for 15 s
python -m apps.hwcheck audio     # play a test tone on `audio.output`, record 3 s from `audio.input`, play it back
python -m apps.hwcheck camera    # grab a frame, print resolution and fps, save to /tmp/hwcheck.jpg
```

It loads drivers through the factory (`carebot.hw.make_hardware`), so it also proves the drivers
plug into the app.

## Testing

- `python -m pytest` must still pass **on a laptop** after your changes. Your modules must import
  there without Pi packages (rule 2).
- Pi-only tests go in `tests/hw/` with `@pytest.mark.pi`. They're skipped unless run with
  `python -m pytest -m pi` on the robot.

## Definition of done, per part

A driver is done when all of these hold:

1. It subclasses the right class in `base.py` with matching signatures.
2. Its `hwcheck` passes on the robot.
3. The laptop `pytest` is green.
4. Its config keys are in `config/hardware_pi.yaml`.
5. You've written a line in `notes/hardware-to-software.md`, for example "PiEyes ready, 12 pixels,
   tested".

Suggested order, matching the software build plan:

1. Wiring and the pin map.
2. `PiButton` and `PiBuzzer` (easiest).
3. `PiEyes`.
4. `PiPanTilt`, with calibration.
5. Audio: the JBL and the mic.
6. `bootstrap.sh`.

Target: all parts done by hour 10.

## Git

- Work on `main`. Pull with rebase before you start and before every commit.
- Make small commits that touch only your files, and push right after each one.
- Never force-push.
- No secrets are involved in hardware. If you ever need one, it goes in `.env`, never in git.
