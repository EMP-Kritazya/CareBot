"""The hardware contract. Every driver (Pi or mock) subclasses these.

Owned by Software. Hardware implements `*_pi.py` against this file. See docs/HARDWARE.md.
Change it only after a note in notes/, and update mock.py and the doc in the same commit.
"""

from abc import ABC, abstractmethod
from typing import Callable, Literal, get_args

EyeState = Literal["off", "boot", "watching", "checking", "alerted", "help_coming", "error"]
EYE_STATES: tuple[str, ...] = get_args(EyeState)


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
        Hobby servos (our Hitec D625MW) have no position feedback, so this is time-based.
        Vision skips frames while it is True."""

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
