"""Face lock rules: which tracked person is Ruth."""

from carebot.config import load_config
from carebot.vision.identity import ResidentLock, head_box
from carebot.vision.synth import person

CFG = load_config("config/mock.yaml")["detectors"]["identity"]
MATCH, REJECT = CFG["match_threshold"], CFG["reject_threshold"]


def lock(**kw) -> ResidentLock:
    return ResidentLock.from_config({**CFG, **kw})


def test_face_match_binds_the_track():
    lk = lock()
    assert lk.update(0, [1, 2], {2: 0.6}) == 2
    assert lk.source == "face"


def test_lock_rides_the_track_while_the_face_is_hidden():
    lk = lock()
    lk.update(0, [1, 2], {2: 0.6})
    for t in range(1, 20):  # she falls; no face visible for 19 s, but her track stays
        assert lk.update(t, [1, 2], {}) == 2


def test_alone_and_not_enrolled_is_assumed_to_be_ruth():
    lk = lock()
    assert lk.update(0, [5], {}) == 5
    assert lk.source == "assumed"


def test_two_people_and_no_face_yet_means_nobody_is_assumed():
    assert lock().update(0, [1, 2], {}) is None


def test_face_overrides_an_assumption():
    lk = lock()
    lk.update(0, [5], {})          # grandson alone: assumed
    assert lk.update(1, [5, 6], {6: 0.7}) == 6  # Ruth walks in and is recognized


def test_clear_non_match_on_the_locked_track_drops_it():
    lk = lock()
    lk.update(0, [5], {})          # assumed
    assert lk.update(1, [5], {5: 0.05}) is None  # his face is clearly not Ruth's
    assert lk.update(2, [5], {}) is None         # and he isn't re-assumed while he's alone


def test_weak_view_does_not_drop_a_face_lock():
    lk = lock()
    lk.update(0, [3], {3: 0.6})
    assert lk.update(1, [3], {3: 0.3}) == 3  # between reject and match: unsure, keep the lock


def test_lock_released_after_track_gone_then_new_track_assumed():
    lk = lock()
    lk.update(0, [3], {3: 0.6})
    assert lk.update(1, [], {}) == 3             # just left
    assert lk.update(1 + CFG["relock_after_s"] + 0.1, [], {}) is None
    assert lk.update(10, [9], {}) == 9           # comes back with a new track id, alone


def test_manual_override_and_unlock():
    lk = lock()
    lk.set_manual(4)
    assert lk.update(0, [4, 5], {}) == 4 and lk.source == "manual"
    lk.set_manual(None)
    assert lk.update(1, [4], {}) is None  # unlock also turns off the single-person assumption


def test_head_box_square_around_face_keypoints():
    p = person(1, (0.5, 0.6))
    box = head_box(p, 1280, 720)
    assert box is not None
    x1, y1, x2, y2 = box
    nose_x, nose_y = p.keypoints[0][0] * 1280, p.keypoints[0][1] * 720
    assert x1 < nose_x < x2 and y1 < nose_y < y2
    assert abs((x2 - x1) - (y2 - y1)) <= 2


def test_head_box_none_when_face_hidden():
    p = person(1, (0.5, 0.6))
    hidden = type(p)(1, tuple((x, y, 0.05 if i < 5 else c) for i, (x, y, c) in enumerate(p.keypoints)))
    assert head_box(hidden, 1280, 720) is None
