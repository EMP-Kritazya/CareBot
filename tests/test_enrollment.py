"""Face saving: new vs add (recalibrate), the sample cap, and the "not the same person" guard."""

import numpy as np

from carebot.vision import enrollment
from carebot.vision.enrollment import Enroller, merge_samples, save_face


def test_new_replaces_and_add_appends():
    old = np.ones((10, 128), np.float32)
    new = np.zeros((4, 128), np.float32)
    assert merge_samples(old, new, "new").shape == (4, 128)
    assert merge_samples(old, new, "add").shape == (14, 128)


def test_add_keeps_only_the_newest_samples():
    old = np.ones((60, 128), np.float32)
    new = np.zeros((10, 128), np.float32)
    out = merge_samples(old, new, "add", cap=64)
    assert out.shape == (64, 128) and not out[-10:].any()  # newest kept


def test_save_then_add_round_trip(tmp_path):
    path = tmp_path / "faces" / "resident.npz"
    assert save_face(path, "Sandy", np.ones((16, 128)), "new") == 16
    assert save_face(path, "Sandy", np.ones((8, 128)), "add") == 24
    data = np.load(path)
    assert str(data["name"]) == "Sandy" and "updated" in data
    assert save_face(path, "Sandy", np.ones((16, 128)), "new") == 16  # recalibrate from scratch


class FakeMatcher:
    """One face in view; its 'embedding' is a constant vector; similarity is configurable."""

    name = "Sandy"
    ready = True

    def __init__(self, sim=0.9, face_px=120, faces=1):
        self.sim, self.face_px, self.faces = sim, face_px, faces

    def detect(self, image):
        return [np.array([10, 10, self.face_px, self.face_px] + [0] * 10 + [0.95])] * self.faces

    def embed(self, image, face):
        return np.ones((1, 128), np.float32)

    def similarity(self, feature):
        return self.sim


IMG = np.zeros((480, 640, 3), np.uint8)


def collect(enroller, frames=40):
    for _ in range(frames):
        enroller._last = 0  # don't wait between captures in tests
        enroller.step(IMG)
    return enroller


def test_new_face_collects_target_samples_in_pairs():
    e = collect(Enroller(FakeMatcher(), mode="new"))
    assert e.done and len(e.samples) % 2 == 0


def test_add_refuses_a_different_person():
    e = collect(Enroller(FakeMatcher(sim=0.05), mode="add"))
    assert e.samples == []
    _, msg, ok = e.step(IMG)
    assert not ok and "doesn't look like the saved Sandy" in msg


def test_add_accepts_her_from_farther_away():
    e = collect(Enroller(FakeMatcher(sim=0.5, face_px=40), mode="add"))
    assert e.done


def test_new_needs_a_close_face_and_one_person():
    assert collect(Enroller(FakeMatcher(face_px=40), mode="new")).samples == []
    assert collect(Enroller(FakeMatcher(faces=2), mode="new")).samples == []


def test_module_documents_local_only_storage():
    assert "git-ignored" in enrollment.__doc__
