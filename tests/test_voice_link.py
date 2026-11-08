"""Voice without an on-board mic: the speaker/mic report, the laptop mic link (text only), and its gate."""

import threading
import time

from apps.remote_mic import Gate
from carebot.net.bridge import RobotLink
from carebot.net.transport import MemoryTransport
from carebot.voice.devices import parse_wpctl_inspect, pipewire_lines
from carebot.voice.listen import Listener


class FakeRuntime:
    def __init__(self):
        self.publish, self.tickers, self.observers, self.posted = None, [], [], []

    def post(self, e):
        self.posted.append(e)


# ---------------------------------------------------------------- speaker / mic report

JBL = '''id 79, type PipeWire:Interface:Node
    device.api = "bluez5"
  * node.description = "JBL Flip 5"
  * node.name = "bluez_output.AA_BB_CC_DD_EE_FF.1"
'''


def test_reads_wpctl_inspect():
    p = parse_wpctl_inspect(JBL)
    assert p["node.description"] == "JBL Flip 5" and p["device.api"] == "bluez5"


def test_jbl_and_brio_no_warning():
    lines = pipewire_lines({"speaker": parse_wpctl_inspect(JBL), "mic": {"node.description": "Brio 101 Mono"}})
    assert lines[0] == "Speaking through: JBL Flip 5 (Bluetooth)" and "Brio 101" in lines[1]
    assert not any("WARNING" in l for l in lines)


def test_warns_when_the_speaker_is_not_bluetooth():
    lines = pipewire_lines({"speaker": {"node.description": "Dummy Output", "node.name": "auto_null"},
                            "mic": {"node.description": "Brio 101 Mono"}})
    assert any("WARNING" in l and "JBL isn't connected" in l for l in lines)


# ---------------------------------------------------------------- laptop mic link

def test_heard_command_becomes_the_typed_answer():
    tr, rt = MemoryTransport(), FakeRuntime()
    link = RobotLink(tr, rt)
    got = []
    link.on_heard = lambda text: got.append(text) or True
    tr.publish("cmd", {"type": "heard", "text": "  I'm a neighbor,   I'm taking him to the hospital "})
    tr.publish("cmd", {"type": "heard", "text": "x" * 500})
    assert got[0] == "I'm a neighbor, I'm taking him to the hospital"
    assert len(got[1]) == 300


def test_listening_is_announced_on_and_off_around_an_answer():
    tr = MemoryTransport()
    link = RobotLink(tr, FakeRuntime())
    events = []
    tr.subscribe("event", events.append)
    listener = Listener(settle_s=0)
    listener.on_state = link.listening
    threading.Timer(0.05, lambda: listener.type("I'm okay")).start()
    assert listener.listen("keywords", timeout=2) == "I'm okay"
    seen = [(e["on"], e["mode"]) for e in events if e["type"] == "listening"]
    assert seen == [(True, "keywords"), (False, None)]


def test_a_broken_link_never_breaks_listening():
    listener = Listener(settle_s=0)
    listener.on_state = lambda on, mode: 1 / 0
    assert listener.listen("keywords", timeout=0.2) is None


# ---------------------------------------------------------------- gate (pure)

def test_gate_only_while_listening_and_once_per_question():
    g = Gate(skip_s=0.3)
    assert not g.hearing(0.0) and not g.take()          # robot isn't listening
    g.set(True, 10.0)
    assert not g.hearing(10.1)                           # skip the speaker's echo
    assert g.hearing(10.4) and g.take()                  # first answer goes
    assert not g.take() and not g.hearing(10.5)          # only one per question
    g.set(False, 12.0)
    g.set(True, 20.0)                                    # next question: fresh
    assert g.generation == 2 and g.hearing(20.5) and g.take()
