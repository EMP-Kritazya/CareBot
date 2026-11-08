"""Fall and stillness detectors, including the field-of-view rule. Synthetic skeletons at 10 fps."""

import copy
import random

import pytest

from carebot.config import load_config
from carebot.vision import detectors as _det
from carebot.vision.keypoints import features
from carebot.vision.synth import frame, lerp, person

CFG = load_config("config/mock.yaml")["detectors"]
CFG["stillness"]["profile"] = "demo"
# These tests pin their own short timers, so retuning the demo values in YAML doesn't break them.
CFG["stillness"]["timers"]["demo"] = {"sitting": 30, "upright": 20, "slumped": 10}
CFG["stillness"]["cooldown_s"]["demo"] = 60
REAL = copy.deepcopy(CFG)
REAL["stillness"]["profile"] = "real"


def Detectors():
    return _det.Detectors(copy.deepcopy(CFG))


def RealDetectors():
    return _det.Detectors(copy.deepcopy(REAL))


HOLD = CFG["fall"]["hold_s"]
DOWN = 1.5 + HOLD + 1.5  # long enough after a fall at t=1 for the hold to expire

FPS = 10
DT = 1 / FPS


def run(det, frames):
    events = []
    for f in frames:
        events.extend(det.update(f))
    return events


def kinds(events):
    return [e.kind for e in events]


def standing(tid, t0, t1, hip=(0.5, 0.5), resident=None, **kw):
    t = t0
    while t < t1 - 1e-9:
        yield frame(t, person(tid, hip, "upright", **kw), resident=resident)
        t += DT


def falling(tid, t0, dur, start_y=0.5, end_y=0.85, end_deg=90.0, resident=None, x=0.5):
    """Hips drop and the torso rotates over `dur` seconds."""
    n = max(1, round(dur * FPS))
    for i in range(1, n + 1):
        k = i / n
        deg = lerp(5, end_deg, k)
        yield frame(t0 + i * DT, person(tid, (x, lerp(start_y, end_y, k)), "lying" if deg >= 60 else "upright",
                                        torso_deg=deg), resident=resident)


def lying(tid, t0, t1, y=0.85, resident=None, x=0.5):
    t = t0
    while t < t1 - 1e-9:
        yield frame(t, person(tid, (x, y), "lying"), resident=resident)
        t += DT


def empty(t0, t1, *others):
    t = t0
    while t < t1 - 1e-9:
        yield frame(t, *others)
        t += DT


# ---------------------------------------------------------------- falls

def test_fall_seen_and_stays_down_fires_once_after_hold():
    det = Detectors()
    ev = run(det, [*standing(1, 0, 1, resident=1), *falling(1, 1, 0.4, resident=1), *lying(1, 1.5, DOWN, resident=1)])
    assert kinds(ev) == ["fall_candidate", "fall"]
    cand, fall = ev
    assert fall.who == "resident" and not fall.out_of_view
    assert HOLD - 0.1 <= fall.ts - cand.ts <= HOLD + 0.2


def test_fall_then_gets_back_up_is_recovered_not_alerted():
    det = Detectors()
    up_at = 1.5 + HOLD - 1  # gets up just before the hold runs out
    ev = run(det, [*standing(1, 0, 1), *falling(1, 1, 0.4), *lying(1, 1.5, up_at), *standing(1, up_at, up_at + 5)])
    assert kinds(ev) == ["fall_candidate", "fall_recovered"]


def test_sitting_down_hard_is_not_a_fall():
    det = Detectors()
    frames = [*standing(1, 0, 1)]
    for i in range(1, 4):  # hips drop 0.22 in 0.3 s, torso stays upright
        frames.append(frame(1 + i * DT, person(1, (0.5, lerp(0.5, 0.72, i / 3)), "sitting")))
    frames += [frame(1.3 + i * DT, person(1, (0.5, 0.72), "sitting")) for i in range(1, 60)]
    assert run(det, frames) == []


def test_sitting_on_the_floor_upright_after_a_big_drop_is_not_a_fall():
    """Spec: the drop AND the torso angle. Upright on the floor stays a non-event (it's tunable)."""
    det = Detectors()
    frames = [*standing(1, 0, 1)]
    frames += [frame(1 + i * DT, person(1, (0.5, lerp(0.5, 0.85, i / 4)), "sitting")) for i in range(1, 60)]
    assert run(det, frames) == []


def test_lying_down_slowly_on_the_couch_is_not_a_fall():
    det = Detectors()
    ev = run(det, [*standing(1, 0, 1), *falling(1, 1, 4.0, end_y=0.75), *lying(1, 5.1, 15, y=0.75)])
    assert ev == []


def test_fall_out_of_frame_alerts_after_out_of_view_timeout():
    det = Detectors()
    frames = [*standing(1, 0, 1, resident=1), *falling(1, 1, 0.4, end_deg=35, resident=1)]
    frames += list(empty(1.5, 13))  # gone behind the bed
    ev = run(det, frames)
    assert kinds(ev) == ["fall_candidate", "fall"]
    assert ev[1].out_of_view and ev[1].who == "resident"
    assert 9.9 <= ev[1].ts - 1.5 <= 10.2


def test_fall_out_of_frame_then_reappears_upright_with_new_track_id():
    det = Detectors()
    frames = [*standing(1, 0, 1), *falling(1, 1, 0.4, end_deg=35), *empty(1.5, 5)]
    frames += list(standing(7, 5, 8, hip=(0.55, 0.5)))  # tracker gave him a new id
    assert kinds(run(det, frames)) == ["fall_candidate", "fall_recovered"]


def test_lost_mid_fall_then_redetected_low_on_the_floor_is_not_getting_up():
    """URFD fall-01: the tracker loses him lying at the bottom edge, then finds him again (new id)
    with a torso that reads only 23 degrees but hips even lower than where he fell. Still down."""
    det = Detectors()
    frames = [*standing(1, 0, 1), *falling(1, 1, 0.4, end_y=0.8, end_deg=70), *empty(1.4, 1.8)]
    frames += [frame(1.8 + i * DT, person(3, (0.5, 0.88), torso_deg=23)) for i in range(int(12 / DT))]
    assert kinds(run(det, frames)) == ["fall_candidate", "fall"]


def test_walking_out_of_frame_is_not_an_event():
    det = Detectors()
    frames = [frame(i * DT, person(1, (lerp(0.5, 0.98, i / 20), 0.5))) for i in range(20)]
    frames += list(empty(2, 25))
    assert run(det, frames) == []


def test_walking_up_to_the_robot_then_out_of_frame_is_not_a_fall():
    det = Detectors()
    frames = [*standing(1, 0, 1)]
    for i in range(1, 11):  # hips move down the frame but the body grows: approaching
        k = i / 10
        frames.append(frame(1 + i * DT, person(1, (0.5, lerp(0.5, 0.8, k)), torso_len=lerp(0.25, 0.45, k))))
    frames += list(empty(2.1, 20))
    assert run(det, frames) == []


def test_camera_moving_frames_are_skipped():
    det = Detectors()
    frames = [*standing(1, 0, 1)]
    frames += [frame(1 + i * DT, person(1, (0.5, lerp(0.5, 0.85, i / 3)), torso_deg=lerp(5, 70, i / 3)),
                     moving=True) for i in range(1, 4)]
    frames += [frame(1.3 + i * DT, person(1, (0.5, 0.85), torso_deg=70)) for i in range(1, 50)]
    # The head was turning while the hips "dropped"; afterwards the pose is steady. No fall start.
    assert "fall_candidate" not in kinds(run(det, frames))


def test_visitor_fall_is_reported_as_other():
    det = Detectors()
    ruth = list(standing(1, 0, DOWN, hip=(0.3, 0.5), resident=1))
    visitor = [*standing(2, 0, 1, hip=(0.7, 0.5)), *falling(2, 1, 0.4, x=0.7), *lying(2, 1.5, DOWN, x=0.7)]
    frames = [frame(a.ts, *a.people, *b.people, resident=1) for a, b in zip(ruth, visitor)]
    ev = run(det, frames)
    falls = [e for e in ev if e.kind == "fall"]
    assert len(falls) == 1 and falls[0].who == "other" and falls[0].track_id == 2


def test_fall_rearms_after_standing_up():
    det = Detectors()
    t = DOWN
    frames = [*standing(1, 0, 1), *falling(1, 1, 0.4), *lying(1, 1.5, t), *standing(1, t, t + 2),
              *falling(1, t + 2, 0.4), *lying(1, t + 2.5, t + 2.5 + HOLD + 1)]
    assert kinds(run(det, frames)).count("fall") == 2


# ---------------------------------------------------------------- stillness

def sitting_still(tid, t0, t1, resident=1, jitter=0.002, posture="sitting", hip=(0.5, 0.6)):
    rng = random.Random(42)
    t = t0
    while t < t1 - 1e-9:
        yield frame(t, person(tid, hip, posture, jitter=jitter, rng=rng), resident=resident)
        t += DT


def test_resident_still_in_chair_fires_once_after_30s():
    det = Detectors()
    ev = run(det, sitting_still(1, 0, 60))
    assert kinds(ev) == ["stillness"]
    assert 30 <= ev[0].ts <= 32


def test_visitor_still_on_couch_never_fires():
    det = Detectors()
    ev = run(det, sitting_still(2, 0, 120, resident=1))  # resident (1) isn't in view
    assert ev == []


def test_no_resident_lock_never_fires():
    assert run(Detectors(), sitting_still(1, 0, 60, resident=None)) == []


def test_resident_asleep_lying_still_never_fires():
    assert run(Detectors(), sitting_still(1, 0, 120, posture="lying", hip=(0.5, 0.7))) == []


def test_stepping_out_of_view_restarts_the_stillness_timer():
    det = Detectors()
    frames = [*sitting_still(1, 0, 20), *empty(20, 21), *sitting_still(1, 21, 41)]
    assert run(det, frames) == []
    assert kinds(run(det, sitting_still(1, 41, 55))) == ["stillness"]


def test_moving_resident_never_fires():
    det = Detectors()
    frames = [frame(i * DT, person(1, (0.5 + 0.05 * ((i // 5) % 2), 0.6), "sitting"), resident=1)
              for i in range(600)]
    assert run(det, frames) == []


# ---------------------------------------------------------------- posture + geometry

@pytest.mark.parametrize("posture", ["upright", "sitting", "lying"])
def test_resident_posture(posture):
    det = Detectors()
    f = frame(0, person(1, (0.5, 0.6), posture), resident=1)
    assert det.resident_posture(f) == posture


def test_resident_posture_out_of_view():
    det = Detectors()
    assert det.resident_posture(frame(0, person(2, (0.5, 0.6)), resident=1)) == "out_of_view"
    assert det.resident_posture(frame(0)) == "out_of_view"


@pytest.mark.parametrize("aspect", [4 / 3, 16 / 9, 1.0])
def test_torso_angle_is_aspect_independent(aspect):
    feat = features(person(1, (0.5, 0.6), torso_deg=40, aspect=aspect), aspect)
    assert feat.torso_deg == pytest.approx(40, abs=0.5)


# ---------------------------------------------------------------- from live testing (desk, raised hand)

def hide_legs(p, conf=0.1):
    """Hips, knees, ankles hidden (behind a desk)."""
    kps = tuple((x, y, conf if i >= 11 else c) for i, (x, y, c) in enumerate(p.keypoints))
    return type(p)(p.track_id, kps)


def jumpy_hidden_legs(p, rng):
    """Hidden joints get low-confidence guesses that jump around every frame."""
    kps = tuple((x + rng.uniform(-0.1, 0.1), y + rng.uniform(-0.1, 0.1), 0.35) if i >= 11 else (x, y, c)
                for i, (x, y, c) in enumerate(p.keypoints))
    return type(p)(p.track_id, kps)


def test_still_at_a_desk_with_hips_hidden_fires_stillness():
    det = Detectors()
    rng = random.Random(1)
    frames = [frame(i * DT, hide_legs(person(1, (0.5, 0.9), "sitting", torso_len=0.45, jitter=0.002, rng=rng)),
                    resident=1) for i in range(400)]
    ev = run(det, frames)
    assert kinds(ev) == ["stillness"]
    assert det.stillness.reason.startswith("still")


def test_jumpy_low_confidence_hips_do_not_count_as_movement():
    det = Detectors()
    rng = random.Random(2)
    frames = [frame(i * DT, jumpy_hidden_legs(person(1, (0.5, 0.7), "sitting", jitter=0.002, rng=rng), rng),
                    resident=1) for i in range(400)]
    assert "stillness" in kinds(run(det, frames))


def test_upper_body_counts_as_upright_for_the_button():
    det = Detectors()
    f = frame(0, hide_legs(person(1, (0.5, 0.9), "sitting", torso_len=0.45)), resident=1)
    assert det.resident_posture(f) == "upright"


def test_hud_reason_explains_why_timer_is_off():
    det = Detectors()
    run(det, sitting_still(1, 0, 3, posture="lying", hip=(0.5, 0.7)))
    assert "lying" in det.stillness.reason
    run(det, empty(3, 4))
    assert det.stillness.reason == "resident not in view"


# ---------------------------------------------------------------- sofa (from live testing)

def test_quick_fall_onto_sofa_triggers():
    """Hips only drop to seat height (~0.6 torso lengths) but the body tips over fast."""
    det = Detectors()
    frames = [*standing(1, 0, 1), *falling(1, 1, 0.5, end_y=0.65, end_deg=70), *lying(1, 1.6, DOWN, y=0.65)]
    assert kinds(run(det, frames)) == ["fall_candidate", "fall"]


def test_sit_on_sofa_then_lie_back_slowly_does_not_trigger():
    det = Detectors()
    frames = [*standing(1, 0, 1)]
    # Sit down: hips drop to seat height in 0.6 s, torso stays upright.
    frames += [frame(1 + i * DT, person(1, (0.5, lerp(0.5, 0.65, i / 6)), "sitting")) for i in range(1, 7)]
    frames += [frame(1.6 + i * DT, person(1, (0.5, 0.65), "sitting")) for i in range(1, 10)]
    # Then lean back to lying over 2.5 s; hips barely move.
    frames += [frame(2.5 + i * DT, person(1, (0.5, lerp(0.65, 0.68, i / 25)), torso_deg=lerp(5, 85, i / 25)))
               for i in range(1, 26)]
    frames += list(lying(1, 5.1, 12, y=0.68))
    assert run(det, frames) == []


def test_same_fall_far_from_camera_still_triggers():
    """A person 2x farther away looks half the size; the drop is measured in body sizes."""
    det = Detectors()
    small = dict(torso_len=0.12)
    frames = [frame(i * DT, person(1, (0.5, 0.5), **small)) for i in range(10)]
    frames += [frame(1 + i * DT, person(1, (0.5, lerp(0.5, 0.6, i / 4)), "lying" if i >= 3 else "upright",
                                        torso_deg=lerp(5, 80, i / 4), **small)) for i in range(1, 5)]
    frames += [frame(1.4 + i * DT, person(1, (0.5, 0.6), "lying", **small)) for i in range(1, int(DOWN * FPS))]
    assert kinds(run(det, frames)) == ["fall_candidate", "fall"]



# ---------------------------------------------------------------- getting up after a confirmed fall

def test_getting_up_after_a_confirmed_fall_reports_got_up():
    det = Detectors()
    frames = [*standing(1, 0, 1, resident=1), *falling(1, 1, 0.4, resident=1), *lying(1, 1.5, DOWN, resident=1),
              *standing(1, DOWN, DOWN + 3, resident=1)]
    assert kinds(run(det, frames)) == ["fall_candidate", "fall", "got_up"]


def test_out_of_view_fall_then_reappears_upright_reports_got_up():
    det = Detectors()
    oov = CFG["fall"]["out_of_view_s"]
    frames = [*standing(1, 0, 1), *falling(1, 1, 0.4, end_deg=35), *empty(1.5, 1.5 + oov + 1)]
    frames += list(standing(9, 1.5 + oov + 1, 1.5 + oov + 4, hip=(0.55, 0.5)))  # back, with a new track id
    ev = run(det, frames)
    assert kinds(ev) == ["fall_candidate", "fall", "got_up"]
    assert ev[-1].track_id == 1  # reported for the person who fell



# ---------------------------------------------------------------- adaptive stillness

def still_frames(t0, t1, posture="sitting", fps=1, hide_hips=False, hip=(0.5, 0.6), **kw):
    """A resident (track 1) holding still from t0 to t1."""
    rng = random.Random(7)
    t = t0
    while t < t1 - 1e-9:
        p = person(1, hip, posture, jitter=0.002, rng=rng, **kw)
        yield frame(t, hide_legs(p) if hide_hips else p, resident=1)
        t += 1 / fps


def slumped_person(hip=(0.5, 0.6)):
    """Seated, leaning over to one side in the chair (torso 40 degrees)."""
    return person(1, hip, "sitting", torso_deg=40)


def head_hanging_hips_hidden():
    """Chest-up view, head dropped forward below the shoulder line."""
    p = hide_legs(person(1, (0.5, 0.9), "sitting", torso_len=0.45))
    kps = list(p.keypoints)
    sy = (kps[5][1] + kps[6][1]) / 2
    sx = (kps[5][0] + kps[6][0]) / 2
    for i in range(5):  # head drops to just below the shoulders, centered between them
        kps[i] = (sx + (kps[i][0] - kps[0][0]), sy + 0.02, kps[i][2])
    return type(p)(1, tuple(kps))


def lying_in_bed_hips_hidden():
    """In bed under a blanket: shoulders stacked (on one side), head off to the side."""
    p = hide_legs(person(1, (0.5, 0.7), "lying"))
    return p


def test_real_timer_sitting_calmly_waits_20_minutes():
    det = RealDetectors()
    assert run(det, still_frames(0, 5 * 60)) == []           # 5 min watching TV: nothing
    ev = run(det, still_frames(5 * 60, 21 * 60))
    assert kinds(ev) == ["stillness"] and 20 * 60 <= ev[0].ts <= 20 * 60 + 3


def test_real_timer_standing_still_is_3_minutes():
    det = RealDetectors()
    ev = run(det, still_frames(0, 4 * 60, posture="upright", hip=(0.5, 0.5)))
    assert kinds(ev) == ["stillness"] and 180 <= ev[0].ts <= 183


def test_real_timer_slumped_is_1_minute():
    det = RealDetectors()
    rng = random.Random(3)
    frames = []
    for i in range(90):
        p = slumped_person()
        kps = tuple((x + rng.uniform(-0.002, 0.002), y + rng.uniform(-0.002, 0.002), c) for x, y, c in p.keypoints)
        frames.append(frame(float(i), type(p)(1, kps), resident=1))
    ev = run(det, frames)
    assert kinds(ev) == ["stillness"] and 60 <= ev[0].ts <= 62


def test_slump_detection():
    from carebot.vision.keypoints import body_view
    slump = CFG["stillness"]["slump"]
    aspect = 4 / 3
    assert body_view(slumped_person(), aspect, slump=slump)[0] == "slumped"
    assert body_view(head_hanging_hips_hidden(), aspect, 0.3, slump=slump)[0] == "slumped"
    assert body_view(person(1, (0.5, 0.6), "sitting"), aspect, slump=slump)[0] == "sitting"
    assert body_view(person(1, (0.5, 0.5), "upright"), aspect, slump=slump)[0] == "upright"
    # Lying in bed with the hips hidden is never "slumped" (that would wake him every night).
    assert body_view(lying_in_bed_hips_hidden(), aspect, 0.3, slump=slump)[0] == "lying"


def test_cooldown_blocks_stillness_then_timer_runs_normally():
    det = Detectors()  # demo: sitting 30 s, cooldown 60 s
    det.stillness.restart(0.0, cooldown=True)
    assert run(det, still_frames(0, 59)) == []
    assert "cooldown" in det.stillness.reason or det.stillness.reason.startswith("still")
    ev = run(det, still_frames(59, 100))
    assert kinds(ev) == ["stillness"] and 60 + 30 <= ev[0].ts <= 60 + 33


def test_backoff_doubles_caps_and_resets_after_standing_up():
    det = Detectors()
    st = det.stillness
    st.restart(0, backoff=True)
    assert st.threshold("sitting") == 60
    st.restart(0, backoff=True)
    st.restart(0, backoff=True)
    assert st.threshold("sitting") == 120  # capped at 4x
    run(det, still_frames(0, 3))
    run(det, still_frames(3, 6, posture="upright", hip=(0.5, 0.5)))  # stands up
    assert st.threshold("sitting") == 30
