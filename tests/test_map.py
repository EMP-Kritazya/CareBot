"""The map link: neighbors in waves, the robot's bridge, and the map server (no network)."""

import time

import pytest

from carebot.config import load_config
from carebot.core import state as st
from carebot.core.scenario import Scenario
from carebot.escalate.receivers import Receiver, SimReceivers, Waves, load_receivers, load_routes, rank, walk_s
from carebot.net.bridge import RobotLink, attach_map
from carebot.net.transport import MemoryTransport

HOME = {"lat": 40.807745, "lng": -73.966123}


def people(n=7):
    return [Receiver(id=f"r{i}", name=f"Person {i}", lat=HOME["lat"] + 0.0005 * (i + 1), lng=HOME["lng"]) for i in range(n)]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def incident():
    s = Scenario()
    s.fall()
    return s.incident


def make_waves(n=7, **kw):
    sent, clock = [], Clock()
    w = Waves(rank(HOME, people(n), {}), publish=sent.append, wave_size=3, wave_s=90, clock=clock, **kw)
    return w, sent, clock


# ---------------------------------------------------------------- config + ranking

def test_receivers_config_reads_bare_yes_no_and_ranks_by_walking_time():
    rc = load_receivers()
    replies = {r.id: r.sim_reply for r in rc["receivers"]}
    assert replies["krit"] == "yes" and replies["ana"] == "no" and replies["dev"] == "none"
    ranked = rank(rc["home"], rc["receivers"], load_routes())
    times = [walk_s(rc["home"], r, load_routes()) for r in ranked]
    assert times == sorted(times) and ranked[0].id == "krit"
    assert all(not hasattr(r, "phone") for r in rc["receivers"])  # numbers live in .env, never in the yaml


def test_no_route_falls_back_to_a_straight_line_estimate():
    r = people(1)[0]
    assert 40 < walk_s(HOME, r, {}) < 80          # ~56 m * detour / walking speed
    assert walk_s(HOME, r, {"r0": {"walk_s": 300}}) == 300


# ---------------------------------------------------------------- waves

def test_first_wave_is_the_three_nearest_then_the_next_wave_after_the_wait():
    w, sent, clock = make_waves()
    assert w.start(incident())
    assert [k for k, v in w.statuses.items() if v == "contacted"] == ["r0", "r1", "r2"]
    clock.t += 89
    w.tick(active=True)
    assert w.wave == 1
    clock.t += 2
    w.tick(active=True)
    assert w.wave == 2 and w.statuses["r0"] == "no_reply" and w.statuses["r3"] == "contacted"
    assert sent[-1]["wave"] == 2


def test_yes_stands_down_only_those_texted_and_stops_the_waves():
    w, sent, clock = make_waves()
    w.start(incident())
    w.on_event(st.ReceiverReply("r1", "Person 1", None, "yes", clock.t))
    assert w.statuses["r1"] == "yes"
    assert w.statuses["r0"] == w.statuses["r2"] == "stood_down"
    assert w.statuses["r5"] == "waiting"            # never texted, so never told anything
    clock.t += 500
    w.tick(active=True)
    assert w.wave == 1                               # no more waves after a YES


def test_everyone_in_a_wave_saying_no_sends_the_next_wave_at_once():
    w, _, clock = make_waves()
    w.start(incident())
    for rid in ("r0", "r1", "r2"):
        w.on_event(st.ReceiverReply(rid, rid, None, "no", clock.t))
    w.tick(active=True)
    assert w.wave == 2


def test_the_button_pauses_the_wave_clock():
    w, _, clock = make_waves()
    w.start(incident())
    w.pause()
    clock.t += 200
    w.tick(active=True)
    assert w.wave == 1
    w.resume()
    clock.t += 89
    w.tick(active=True)
    assert w.wave == 1
    clock.t += 2
    w.tick(active=True)
    assert w.wave == 2


def test_closing_the_incident_ends_the_waves():
    w, _, clock = make_waves()
    w.start(incident())
    w.tick(active=False)
    clock.t += 500
    w.tick(active=True)
    assert w.wave == 1


def test_a_real_phone_is_called_and_marked_real_so_the_map_never_simulates_it():
    calls = []
    sent, clock = [], Clock()
    krit = Receiver(id="krit", name="Krit", lat=HOME["lat"] + 0.0002, lng=HOME["lng"], channel="call",
                    phone_env="NEIGHBOR_PHONE")
    w = Waves([krit] + people(2), publish=sent.append, clock=clock, env={"NEIGHBOR_PHONE": "+15550000002"},
              contact_call=lambda r, inc: calls.append(r.id) or True)
    w.start(incident())
    assert calls == ["krit"] and sent[-1]["real"] == ["krit"]


def test_a_real_phone_without_twilio_is_simulated():
    sent, clock = [], Clock()
    krit = Receiver(id="krit", name="Krit", lat=HOME["lat"], lng=HOME["lng"], channel="call", phone_env="NEIGHBOR_PHONE")
    w = Waves([krit], publish=sent.append, clock=clock, env={"NEIGHBOR_PHONE": "+1555"}, contact_call=None)
    assert w.start(incident()) and sent[-1]["real"] == []


def test_all_waves_used_up_ends_quietly():
    w, _, clock = make_waves(n=3)
    w.start(incident())
    clock.t += 91
    w.tick(active=True)
    assert w.done and all(v == "no_reply" for v in w.statuses.values())


# ---------------------------------------------------------------- simulated neighbors

def test_simulated_neighbors_reply_as_configured_and_never_after_being_stood_down():
    replies = []
    rs = [Receiver("a", "A", 0, 0, sim_reply="yes", sim_after_s=0.05),
          Receiver("b", "B", 0, 0, sim_reply="no", sim_after_s=0.2),
          Receiver("c", "C", 0, 0, sim_reply="none")]
    sim = SimReceivers(rs, reply=lambda rid, ans: replies.append((rid, ans)))
    sim.on_receivers({"incident_id": "i", "statuses": {"a": "contacted", "b": "contacted", "c": "contacted"}})
    time.sleep(0.1)
    sim.on_receivers({"incident_id": "i", "statuses": {"a": "yes", "b": "stood_down", "c": "stood_down"}})
    time.sleep(0.25)
    assert replies == [("a", "yes")]


def test_simulator_leaves_real_phones_alone():
    replies = []
    sim = SimReceivers([Receiver("krit", "Krit", 0, 0, sim_reply="yes", sim_after_s=0.01)],
                       reply=lambda rid, ans: replies.append(rid))
    sim.on_receivers({"incident_id": "i", "statuses": {"krit": "contacted"}, "real": ["krit"]})
    time.sleep(0.05)
    assert replies == []


# ---------------------------------------------------------------- the robot's bridge

class FakeRuntime:
    def __init__(self):
        self.state = st.Robot()
        self.eyes = "watching"
        self.posted, self.tickers, self.observers = [], [], []
        self.publish = None

    def post(self, e):
        self.posted.append(e)


def test_bridge_sends_events_with_sender_and_time_and_takes_commands():
    tr, rt = MemoryTransport(), FakeRuntime()
    seen = []
    tr.subscribe("event", seen.append)
    link = RobotLink(tr, rt, "robot-4A")
    rt.publish({"type": "alert", "incident_id": "inc-1", "event": "fall", "who": "resident", "ts": 1794000000.0})
    assert seen[0]["sender"] == "robot-4A" and seen[0]["ts"].startswith("2026-")
    link.tick()
    assert seen[-1]["type"] == "heartbeat" and seen[-1]["mode"] == "MONITORING" and seen[-1]["eyes"] == "watching"
    tr.publish("cmd", {"type": "trigger"})
    tr.publish("cmd", {"type": "reset"})
    tr.publish("cmd", {"type": "launch_missiles"})
    assert [type(e).__name__ for e in rt.posted] == ["ManualTrigger", "Reset"]


def test_only_json_with_no_frames_or_faces_goes_out():
    tr, rt = MemoryTransport(), FakeRuntime()
    seen = []
    tr.subscribe("event", seen.append)
    RobotLink(tr, rt).publish_event({"type": "update", "incident_id": "i", "status": "hospital", "quote": "ok", "ts": 1.0})
    allowed = {"type", "incident_id", "status", "quote", "ts", "sender", "source", "helper", "event", "who", "mode",
               "eyes", "outcome", "channels", "target", "neighbor", "awaiting", "ask", "kind", "channel", "simulated"}
    assert set(seen[0]) <= allowed


def test_attach_map_wires_waves_into_the_console_notifier_and_simulated_replies_reach_the_robot():
    from carebot.escalate.notifier import ConsoleNotifier
    cfg = load_config()
    tr, rt = MemoryTransport(), FakeRuntime()
    n = ConsoleNotifier(cfg["profile"], rt.post)
    link = attach_map(rt, n, tr, cfg, demo=True)
    assert n.neighbors is link.waves and link.waves.wave_s == 25 and link.waves.ranked[0].id == "krit"
    assert link.waves.on_event in rt.observers
    tr.publish("cmd", {"type": "receiver_reply", "id": "krit", "reply": "yes"})
    e = rt.posted[-1]
    assert isinstance(e, st.ReceiverReply) and e.receiver_id == "krit" and e.name == "Krit" and e.reply == "yes"


# ---------------------------------------------------------------- the map server

@pytest.fixture
def client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from dashboard.server import create_app
    tr = MemoryTransport()
    cmds = []
    tr.subscribe("cmd", cmds.append)
    app = create_app(tr, load_config(), simulate=False)
    return TestClient(app), tr, cmds


def test_map_serves_the_page_and_the_neighbors_without_phone_numbers(client):
    c, _, _ = client
    assert c.get("/").status_code == 200
    conf = c.get("/api/config").json()
    assert conf["resident"] == "Sandy" and len(conf["receivers"]) == 10
    assert conf["receivers"][0]["rank"] == 1 and "phone" not in str(conf).lower().replace("phone_env", "")
    assert conf["routes"]["krit"]["geometry"]


def test_map_buttons_send_only_trigger_and_reset(client):
    c, _, cmds = client
    assert c.post("/api/cmd/trigger").json() == {"ok": True}
    assert c.post("/api/cmd/reset").status_code == 200
    assert c.post("/api/cmd/receiver_reply").status_code == 400
    assert cmds == [{"type": "trigger"}, {"type": "reset"}]


def test_a_page_opened_late_gets_the_story_so_far(client):
    c, tr, _ = client
    tr.publish("event", {"type": "alert", "incident_id": "inc-1", "event": "fall", "who": "resident"})
    tr.publish("receivers", {"type": "receivers", "incident_id": "inc-1", "wave": 1, "statuses": {"krit": "contacted"}})
    tr.publish("event", {"type": "heartbeat", "mode": "ALERTED"})
    with c.websocket_connect("/ws") as ws:
        got = [ws.receive_json() for _ in range(3)]
    assert [g["msg"]["type"] for g in got] == ["alert", "receivers", "heartbeat"]



# ---------------------------------------------------------------- the real app, end to end (Twilio faked)

class _Calls:
    def __init__(self):
        self.made = []

    def create(self, **kw):
        self.made.append(kw)
        return type("Call", (), {"sid": f"CA{len(self.made):032d}"})()

    def __call__(self, sid):
        return type("One", (), {"fetch": lambda _s: type("C", (), {"status": "in-progress"})(),
                                "update": lambda _s, **kw: self.made.append({"update": sid, **kw})})()


class _Messages:
    def __init__(self):
        self.sent = []

    def create(self, **kw):
        self.sent.append(kw)
        return type("Msg", (), {"sid": f"SM{len(self.sent):032d}"})()


def test_real_app_alert_calls_911_texts_the_son_rings_nearest_and_a_yes_updates_both(monkeypatch):
    """What the robot really does with Twilio: 911 call, WhatsApp to Suyog, the nearest neighbor with a
    phone is called; Krit's YES -> Suyog gets a WhatsApp update and 911 hears it in the open call."""
    from carebot.escalate.twilio_calls import TwilioNotifier
    from carebot.runtime import Runtime
    from carebot.core.script import Profile
    from carebot.hw import make_hardware
    import threading
    cfg = load_config()
    env = {"TWILIO_FROM_NUMBER": "+15550000000", "FAMILY_PHONE": "+15550000001", "NEIGHBOR_PHONE": "+15550000002",
           "PSAP_STANDIN_PHONE": "+15550000003"}
    monkeypatch.setenv("NEIGHBOR_PHONE", env["NEIGHBOR_PHONE"])
    client = type("Client", (), {})()
    client.calls, client.messages = _Calls(), _Messages()
    tr = MemoryTransport()
    events, rx = [], []
    tr.subscribe("event", events.append)
    tr.subscribe("receivers", rx.append)
    rt: Runtime
    n = TwilioNotifier(cfg["profile"], lambda e: rt.post(e), client=client, env=env, public_url="https://x.example",
                       family_via="text", text_via="whatsapp")
    quiet = {p: {"verbose": False} for p in ("pantilt", "eyes", "buzzer", "button")}

    class Voice:
        last = None

        def submit(self, a):
            if isinstance(a, st.Ask):
                Voice.last = a
    rt = Runtime(st.Machine(Profile.from_config(cfg)), make_hardware("mock", quiet), Voice(), n,
                 posture=lambda: "lying", narrate=False)
    link = attach_map(rt, n, tr, cfg, demo=True)
    link.waves.env = {"NEIGHBOR_PHONE": env["NEIGHBOR_PHONE"]}

    def settle():
        for _ in range(40):
            rt.pump()
            for t in threading.enumerate():
                if t is not threading.current_thread() and t.daemon and t.name.startswith("Thread"):
                    t.join(timeout=0.05)
        rt.pump()

    rt.post(st.ManualTrigger(time.time()))
    rt.pump()
    for _ in range(2):
        rt.post(st.Silence(Voice.last.ask_id, time.time()))
        rt.pump()
    settle()
    called = [c["to"] for c in client.calls.made if "to" in c]
    assert env["PSAP_STANDIN_PHONE"] in called and env["NEIGHBOR_PHONE"] in called
    assert env["FAMILY_PHONE"] not in called                                  # family: text only
    assert client.messages.sent[0]["to"] == "whatsapp:" + env["FAMILY_PHONE"]
    assert rx[0]["statuses"]["krit"] == "contacted" and rx[0]["real"] == ["krit"]
    shown = [(e["target"], e["status"]) for e in events if e["type"] == "dispatch"]
    assert ("911", "dialing") in shown and ("family", "sending") in shown

    # Krit presses 1 on his real call (the webhook posts this)
    rt.post(st.ReceiverReply("krit", "Krit", "115th Street", "yes", time.time()))
    settle()
    assert rx[-1]["statuses"]["krit"] == "yes"
    update = client.messages.sent[-1]
    assert update["to"] == "whatsapp:" + env["FAMILY_PHONE"] and "Krit" in update["body"] and "replied YES" in update["body"]
    spoken = [c for c in client.calls.made if "update" in c]
    assert spoken and "Krit" in spoken[-1]["twiml"]                           # 911 hears it in the open call
    assert any(e["type"] == "update" and e.get("helper") == "Krit" for e in events)


def test_console_mode_marks_its_delivery_steps_as_simulated():
    from carebot.escalate.notifier import ConsoleNotifier
    seen = []
    n = ConsoleNotifier({"resident_name": "Sandy"}, lambda e: None, send_delay_s=0.01)
    n.report = lambda **kw: seen.append(kw)
    n._play("911", "alert", "call", [(0, "dialing")])
    time.sleep(0.05)
    assert seen == [{"target": "911", "kind": "alert", "channel": "call", "status": "dialing", "simulated": True}]


def test_twilio_call_progress_reaches_the_map():
    from carebot.escalate.twilio_hooks import handle
    seen = []
    handle("/twilio/status", {"inc": "i", "target": "911", "kind": "alert"}, {"CallStatus": "ringing"},
           lambda e: None, {}, None, None, None, lambda **kw: seen.append(kw))
    handle("/twilio/sms-status", {"label": "Suyog (family)", "target": "family", "kind": "alert", "channel": "whatsapp"},
           {"MessageStatus": "read"}, lambda e: None, {}, None, None, None, lambda **kw: seen.append(kw))
    assert seen == [{"target": "911", "kind": "alert", "channel": "call", "status": "ringing"},
                    {"target": "family", "kind": "alert", "channel": "whatsapp", "status": "read"}]
