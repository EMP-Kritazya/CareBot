"""Face lock rules: which tracked person is Sandy."""

from carebot.config import load_config
from carebot.vision.identity import ResidentLock, head_box
from carebot.vision.synth import person

CFG = load_config("config/mock.yaml")["detectors"]["identity"]
MATCH, REJECT = CFG["match_threshold"], CFG["reject_threshold"]
GOOD, WEAK, STRANGER = MATCH + 0.2, (MATCH + REJECT) / 2, REJECT - 0.1


def lock(enrolled=True, **kw) -> ResidentLock:
    return ResidentLock.from_config({**CFG, **kw}, enrolled=enrolled)


def test_two_matches_in_a_row_bind_the_track():
    lk = lock()
    lk.update(0, [1, 2], {2: GOOD})
    assert lk.source != "face"                                # one match isn't enough
    assert lk.update(1, [1, 2], {2: GOOD}) == 2 and lk.source == "face"


def test_one_lucky_match_does_not_move_sandy_to_a_friend():
    """Reported: the friend was labeled Sandy too."""
    lk = lock()
    lk.update(0, [1, 2], {1: GOOD})
    lk.update(1, [1, 2], {1: GOOD})                          # Sandy (track 1) confirmed
    assert lk.update(2, [1, 2], {2: GOOD, 1: GOOD}) == 1     # friend matches once; Sandy still matches
    assert lk.update(3, [1, 2], {2: WEAK}) == 1
    assert lk.track_id == 1


def test_friend_alone_with_a_non_matching_face_is_not_assumed():
    """Reported: alone in view, the friend was assumed to be Sandy."""
    lk = lock(enrolled=True)
    assert lk.update(0, [7], {}) == 7 and lk.source == "assumed"   # no face seen yet: assumed
    assert lk.update(1, [7], {7: WEAK}) is None                     # face seen, not Sandy's: dropped
    assert lk.update(2, [7], {}) is None                            # and not re-assumed


def test_assumption_still_works_without_an_enrolled_face():
    lk = lock(enrolled=False)
    assert lk.update(0, [7], {}) == 7
    assert lk.update(1, [7], {7: WEAK}) == 7  # nothing to compare against yet


def test_lock_rides_the_track_while_the_face_is_hidden():
    lk = lock()
    lk.update(0, [1, 2], {2: GOOD})
    lk.update(1, [1, 2], {2: GOOD})
    for t in range(2, 20):  # he falls; no face visible, but his track stays
        assert lk.update(t, [1, 2], {}) == 2


def test_tie_keeps_the_current_lock():
    lk = lock()
    for t in (0, 1):
        lk.update(t, [1, 2], {1: GOOD})
    for t in (2, 3):
        lk.update(t, [1, 2], {1: GOOD, 2: GOOD + 0.02})  # both match, within the margin
    assert lk.track_id == 1


def test_face_overrides_an_assumption():
    lk = lock()
    lk.update(0, [5], {})                        # someone alone: assumed
    lk.update(1, [5, 6], {6: GOOD})
    assert lk.update(2, [5, 6], {6: GOOD}) == 6  # Sandy walks in and is recognized twice


def test_clear_stranger_on_the_locked_track_drops_it():
    lk = lock()
    lk.update(0, [5], {})
    assert lk.update(1, [5], {5: STRANGER}) is None
    assert lk.update(2, [5], {}) is None


def test_weak_view_does_not_drop_a_face_lock():
    lk = lock()
    lk.update(0, [3], {3: GOOD})
    lk.update(1, [3], {3: GOOD})
    assert lk.update(2, [3], {3: WEAK}) == 3  # unsure, keep the lock


def test_lock_released_after_track_gone_then_new_track_assumed():
    lk = lock()
    lk.update(0, [3], {3: GOOD})
    lk.update(0.5, [3], {3: GOOD})
    assert lk.update(1, [], {}) == 3
    assert lk.update(1 + CFG["relock_after_s"] + 0.1, [], {}) is None
    assert lk.update(10, [9], {}) == 9  # comes back with a new track id, alone, face not seen yet


def test_manual_override_and_unlock():
    lk = lock()
    lk.set_manual(4)
    assert lk.update(0, [4, 5], {}) == 4 and lk.source == "manual"
    lk.set_manual(None)
    assert lk.update(1, [4], {}) is None


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
