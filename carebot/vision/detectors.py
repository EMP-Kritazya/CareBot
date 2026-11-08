"""Fall and stillness detectors. Pure: frames in, events out. Thresholds come from YAML.

Field-of-view rule (PLAN.md): CareBot acts only on what it sees happen.
- Someone walking out of frame is not an event.
- A fall alerts only when the camera SEES the drop and the person then doesn't get back up:
  they stay down for `hold_s`, or they vanish from frame during or right after the drop and
  don't reappear upright within `out_of_view_s`.
- Stillness counts only for the resident, only while in view, only sitting or upright
  (lying still is sleeping; lying on the floor is the fall detector's job).
- Frames where the camera is moving are skipped for anything motion-based.

Every threshold comes from config/detectors.yaml; none are hard-coded here.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from carebot.vision.keypoints import Features, Frame, Person, body_view, features, motion

# fall_recovered: got up before the hold ran out (no check-in).
# got_up: got up AFTER the fall was confirmed (CareBot is already checking in or alerting).
EventKind = Literal["fall_candidate", "fall", "fall_recovered", "got_up", "stillness"]


@dataclass(frozen=True)
class VisionEvent:
    kind: EventKind
    track_id: int
    who: Literal["resident", "other"]
    ts: float
    out_of_view: bool = False


# ---------------------------------------------------------------- fall

@dataclass
class _FallTrack:
    history: deque = field(default_factory=deque)  # (ts, hip_y, torso_deg, torso_len)
    state: Literal["watch", "candidate", "confirmed"] = "watch"
    candidate_ts: float = 0.0
    top_hip_y: float = 0.0
    drop: float = 0.0
    last_seen: float = 0.0
    last_hip_x: float = 0.5
    vanished_at: float | None = None
    pending_drop: tuple[float, float, float] | None = None  # (ts, top_hip_y, drop) without torso yet
    was_resident: bool = False


class FallDetector:
    def __init__(self, cfg: dict) -> None:
        self.c = cfg
        self.f = cfg["fall"]
        self.tracks: dict[int, _FallTrack] = {}

    def update(self, frame: Frame) -> list[VisionEvent]:
        events: list[VisionEvent] = []
        seen: set[int] = set()
        for person in frame.people:
            feat = features(person, frame.aspect, self.c["min_kp_conf"], self.c["lying_deg"],
                            self.c["sitting_knee_ratio"])
            if feat is None:
                continue
            seen.add(person.track_id)
            tr = self.tracks.get(person.track_id)
            if tr is None:
                tr = self._adopt(person.track_id, feat, frame, events)
            tr.last_seen, tr.last_hip_x, tr.vanished_at = frame.ts, feat.hip[0], None
            tr.was_resident = person.track_id == frame.resident_track_id
            self._visible(person.track_id, tr, feat, frame, events)
        self._unseen(seen, frame, events)
        return events

    def _who(self, tid: int, tr: _FallTrack, frame: Frame) -> Literal["resident", "other"]:
        return "resident" if tid == frame.resident_track_id or tr.was_resident else "other"

    def _adopt(self, tid: int, feat: Features, frame: Frame, events: list) -> _FallTrack:
        """A new track id. If someone vanished mid-fall (or after a confirmed fall) nearby and this
        person is upright, it's them getting back up (the tracker gave them a new id)."""
        tr = _FallTrack()
        self.tracks[tid] = tr
        if self._is_up(feat, None):
            for old_id, old in list(self.tracks.items()):
                # Up = torso upright AND hips back near standing height, measured against the fall it
                # would end (a lying person re-detected low in the frame can read "upright" by angle).
                if (old is not tr and old.state in ("candidate", "confirmed") and old.vanished_at is not None
                        and abs(old.last_hip_x - feat.hip[0]) <= self.f["reappear_radius"]
                        and self._is_up(feat, old)):
                    kind = "fall_recovered" if old.state == "candidate" else "got_up"
                    events.append(VisionEvent(kind, old_id, self._who(old_id, old, frame), frame.ts))
                    del self.tracks[old_id]
                    break
        return tr

    def _is_up(self, feat: Features, tr: _FallTrack | None) -> bool:
        if feat.torso_deg >= self.f["recover_torso_deg"]:
            return False
        if tr is None:
            return True
        return (feat.hip[1] - tr.top_hip_y) <= (1 - self.f["recover_frac"]) * tr.drop

    def _visible(self, tid: int, tr: _FallTrack, feat: Features, frame: Frame, events: list) -> None:
        who = self._who(tid, tr, frame)
        if tr.state == "candidate":
            if self._is_up(feat, tr):
                tr.state = "watch"
                tr.history.clear()
                events.append(VisionEvent("fall_recovered", tid, who, frame.ts))
            elif frame.ts - tr.candidate_ts >= self.f["hold_s"]:
                tr.state = "confirmed"
                events.append(VisionEvent("fall", tid, who, frame.ts))
            return
        if tr.state == "confirmed":
            if self._is_up(feat, tr):  # back on their feet after a confirmed fall: say so, re-arm
                tr.state = "watch"
                tr.history.clear()
                events.append(VisionEvent("got_up", tid, who, frame.ts))
            return
        if frame.vertical_moving:
            tr.history.clear()  # tilting fakes hip motion; start the window over (panning is fine)
            tr.pending_drop = None
            return
        tr.history.append((frame.ts, feat.hip[1], feat.torso_deg, feat.torso_len))
        while tr.history and frame.ts - tr.history[0][0] > self.f["drop_window_s"]:
            tr.history.popleft()
        top_entry = min(tr.history, key=lambda h: h[1])
        top = top_entry[1]
        drop = feat.hip[1] - top
        # Measured in body sizes (torso lengths at the top of the drop), so the same fall counts
        # whether Sandy is 2 m or 5 m from the camera.
        if drop >= self.f["drop_torso"] * top_entry[3]:
            # Walking toward the camera also moves the hips down the frame, but the body gets
            # bigger. A fall doesn't make the torso grow, so approaching is never a fall start.
            approaching = feat.torso_len > self.f["approach_growth"] * top_entry[3]
            if feat.torso_deg >= self.f["torso_deg"]:
                self._start_candidate(tid, tr, frame.ts, top, drop, events, who)
            elif tr.pending_drop is None and not approaching:
                tr.pending_drop = (frame.ts, top, drop)
        if tr.pending_drop and frame.ts - tr.pending_drop[0] > self.f["vanish_after_drop_s"]:
            tr.pending_drop = None

    def _start_candidate(self, tid, tr, ts, top, drop, events, who) -> None:
        tr.state, tr.candidate_ts, tr.top_hip_y, tr.drop = "candidate", ts, top, drop
        tr.pending_drop = None
        events.append(VisionEvent("fall_candidate", tid, who, ts))

    def _unseen(self, seen: set[int], frame: Frame, events: list) -> None:
        for tid, tr in list(self.tracks.items()):
            if tid in seen:
                continue
            if tr.vanished_at is None:
                tr.vanished_at = frame.ts
                # Fast drop, then gone from frame (fell behind the bed / below the camera).
                if tr.state == "watch" and tr.pending_drop and frame.ts - tr.pending_drop[0] <= self.f["vanish_after_drop_s"]:
                    ts, top, drop = tr.pending_drop
                    self._start_candidate(tid, tr, ts, top, drop, events, self._who(tid, tr, frame))
                    tr.vanished_at = frame.ts
            gone_for = frame.ts - tr.vanished_at
            if tr.state == "candidate" and gone_for >= self.f["out_of_view_s"]:
                tr.state = "confirmed"
                events.append(VisionEvent("fall", tid, self._who(tid, tr, frame), frame.ts, out_of_view=True))
            elif tr.state == "watch" and gone_for > 5:
                del self.tracks[tid]  # just walked away: forget them


# ---------------------------------------------------------------- stillness

TIMER_KIND = {"sitting": "sitting", "upper_body": "sitting", "upright": "upright", "slumped": "slumped"}
KIND_LABEL = {"sitting": "sitting calmly", "upright": "standing", "slumped": "slumped"}


def mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


class StillnessDetector:
    """Resident only, in view only, never lying. Fires once per still spell.

    Adaptive:
    - the timer depends on what the camera sees: sitting calmly (long), standing (medium), slumped (short);
    - after an incident closes, `restart(cooldown=True)` pauses stillness checks for a while;
    - after "I'm OK" to a stillness check, `restart(backoff=True)` makes the next one wait longer,
      until he's up and moving again.
    """

    def __init__(self, cfg: dict) -> None:
        self.c = cfg
        self.s = cfg["stillness"]
        self.samples: deque[tuple[float, Person]] = deque()
        self.track_id: int | None = None
        self.still_since: float | None = None
        self.fired = False
        self.reason = "starting"           # why the timer is (or isn't) running, for the live view
        self.last_motion: float | None = None
        self.kind: str | None = None       # sitting | upright | slumped (current timer)
        self.multiplier = 1.0              # back-off after "I'm OK" to a stillness check
        self.cooldown_until: float | None = None

    @property
    def profile(self) -> str:
        return self.s.get("profile", "real")

    def threshold(self, kind: str) -> float:
        """Seconds of stillness before a check-in, for this kind of stillness right now."""
        return self.s["timers"][self.profile][kind] * self.multiplier

    def restart(self, ts: float, cooldown: bool = False, backoff: bool = False) -> None:
        """An incident just closed (or he just proved he's responsive): start counting from zero.
        cooldown: no stillness checks for cooldown_s. backoff: the next check waits longer."""
        self._reset()
        if cooldown:
            self.cooldown_until = ts + self.s["cooldown_s"][self.profile]
        if backoff:
            self.multiplier = min(self.multiplier * self.s["backoff_factor"], self.s["backoff_max"])

    def _reset(self) -> None:
        self.samples.clear()
        self.still_since = None
        self.fired = False

    def update(self, frame: Frame) -> list[VisionEvent]:
        person = frame.person(frame.resident_track_id)
        self.last_motion = None
        if person is None or person.track_id != self.track_id:
            self._reset()  # out of view, or a different person now: stillness only counts in view
            self.track_id = person.track_id if person else None
            if person is None:
                self.reason = "resident not in view"
                return []
        if frame.camera_moving:
            self._reset()
            self.reason = "camera moving"
            return []
        view = body_view(person, frame.aspect, self.c["min_kp_conf"], self.c["lying_deg"],
                         self.c["sitting_knee_ratio"], self.s.get("slump"))
        if view is None:
            self._reset()
            self.reason = "body not visible enough (need shoulders + head, or hips)"
            return []
        posture, scale = view
        kind = TIMER_KIND.get(posture)
        if self.kind in ("sitting", "slumped") and kind == "upright":
            self.multiplier = 1.0  # stood up: he's active again, back to normal timers
        self.kind = kind
        if posture not in self.s["postures"] or kind is None:
            self._reset()
            self.reason = f"{posture}: timer off" + (" (lying still is sleeping)" if posture == "lying" else "")
            return []
        if self.cooldown_until is not None:
            if frame.ts < self.cooldown_until:
                self._reset()
                self.reason = f"cooldown after check-in: {mmss(self.cooldown_until - frame.ts)} left"
                return []
            self.cooldown_until = None
        # Compare the current pose with every pose from the last window_s and take the biggest
        # movement. Comparing with a single old pose would miss motion that repeats (rocking).
        # Only confident keypoints count: hidden joints get jumpy guesses that look like movement.
        window = self.s["window_s"]
        have_history = bool(self.samples) and frame.ts - self.samples[0][0] >= 0.9 * window
        moves = [motion(old, person, frame.aspect, scale, self.s["min_kp_conf"])
                 for _, old in self.samples]
        self.samples.append((frame.ts, person))
        while self.samples and frame.ts - self.samples[0][0] > window:
            self.samples.popleft()
        moves = [m for m in moves if m is not None]
        if not have_history or not moves:
            self.reason = "warming up"
            return []
        self.last_motion = max(moves)
        label = KIND_LABEL[kind]
        if self.last_motion >= self.s["motion_threshold"]:
            if self.last_motion >= self.s["active_move"]:
                self.multiplier = 1.0  # really moving around (walking): back to normal timers
            self.still_since, self.fired = None, False
            self.reason = f"moving ({label})"
            return []
        if self.still_since is None:
            self.still_since = frame.ts - window
        limit = self.threshold(kind)
        self.reason = f"still ({label}) {mmss(frame.ts - self.still_since)} / {mmss(limit)}"
        if self.multiplier > 1:
            self.reason += f"  (x{self.multiplier:g} after last 'I'm OK')"
        if not self.fired and frame.ts - self.still_since >= limit:
            self.fired = True
            return [VisionEvent("stillness", person.track_id, "resident", frame.ts)]
        return []


# ---------------------------------------------------------------- together

class Detectors:
    def __init__(self, cfg: dict) -> None:
        """cfg: the `detectors` section of a run config (see config/detectors.yaml)."""
        self.cfg = cfg
        self.fall = FallDetector(self.cfg)
        self.stillness = StillnessDetector(self.cfg)

    def update(self, frame: Frame) -> list[VisionEvent]:
        return self.fall.update(frame) + self.stillness.update(frame)

    def resident_posture(self, frame: Frame) -> Literal["upright", "sitting", "lying", "out_of_view"]:
        """What the camera sees of the resident right now (for button attribution)."""
        return self.posture_of(frame, frame.resident_track_id)

    def posture_of(self, frame: Frame, track_id: int | None) -> Literal["upright", "sitting", "lying", "out_of_view"]:
        """What the camera sees of one tracked person right now."""
        person = frame.person(track_id) if track_id is not None else None
        if person is None:
            return "out_of_view"
        view = body_view(person, frame.aspect, self.cfg["min_kp_conf"], self.cfg["lying_deg"],
                         self.cfg["sitting_knee_ratio"], self.cfg["stillness"].get("slump"))
        if view is None:
            return "out_of_view"
        # Head above the shoulders = not on the floor; slumped in a chair = seated.
        return {"upper_body": "upright", "slumped": "sitting"}.get(view[0], view[0])
