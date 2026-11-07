"""Which YOLO detections count as a person. Remark from live testing: a raised hand showed up as a visitor."""

from carebot.config import load_config
from carebot.vision.pose import Detection, filter_people
from carebot.vision.synth import person

CFG = load_config("config/mock.yaml")["detectors"]["pose"]


def det(tid, box, kps_conf=0.9, n_good=17):
    p = person(tid, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2))
    kps = tuple((x, y, kps_conf if i < n_good else 0.1) for i, (x, y, _) in enumerate(p.keypoints))
    return Detection(tid, box, kps)


def test_whole_person_is_kept():
    d = det(1, (0.3, 0.1, 0.6, 0.95))
    assert filter_people([d], CFG) == [d]


def test_raised_hand_inside_a_person_is_dropped():
    me = det(1, (0.3, 0.1, 0.7, 0.95))
    hand = det(2, (0.55, 0.1, 0.68, 0.3))  # small, inside my box
    assert [d.track_id for d in filter_people([me, hand], CFG)] == [1]


def test_detection_with_few_confident_keypoints_is_dropped():
    blob = det(3, (0.1, 0.1, 0.4, 0.6), n_good=3)  # only a wrist/elbow/shoulder
    assert filter_people([blob], CFG) == []


def test_tiny_box_is_dropped():
    assert filter_people([det(4, (0.5, 0.5, 0.55, 0.58))], CFG) == []


def test_two_real_people_side_by_side_are_both_kept():
    a, b = det(1, (0.05, 0.1, 0.45, 0.95)), det(2, (0.5, 0.15, 0.9, 0.95))
    assert len(filter_people([a, b], CFG)) == 2


def test_person_lying_on_floor_is_kept():
    # Wide and short box: a fallen person must never be filtered out.
    assert len(filter_people([det(5, (0.2, 0.75, 0.8, 0.95))], CFG)) == 1
