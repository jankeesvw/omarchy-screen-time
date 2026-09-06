#!/usr/bin/env python3
"""Tests that do not need a running daemon or a desktop.

Run with: python3 tests/test_core.py
"""

import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


def section(title):
    print(f"\n{title}")


# --- the clock ---------------------------------------------------------

def test_clock():
    from screen_time import clock

    section("clock")
    c = clock.Clock()
    c.logical = 1_000_000.0
    c._boot -= 10  # ten seconds of boot time have passed

    real = time.time
    try:
        time.time = lambda: 1_000_010.0
        now, elapsed = c.tick()
        check("follows the wall clock when it agrees", abs(now - 1_000_010.0) < 1 and 9 < elapsed < 11)

        c.logical = 1_000_010.0
        c._boot -= 10
        time.time = lambda: 900_000.0  # somebody set the clock back
        now, _ = c.tick()
        check("ignores a clock set backwards", abs(now - 1_000_020.0) < 1, f"got {now}")
        check("records the jump", c.jumps == 1 and c.last_jump["drift_seconds"] < 0)

        c.logical = 1_000_020.0
        c._boot -= 10
        time.time = lambda: 1_100_000.0  # a day forward, to get a fresh budget
        now, _ = c.tick()
        check("ignores a clock set forward", abs(now - 1_000_030.0) < 1, f"got {now}")
    finally:
        time.time = real

    c2 = clock.Clock(floor=time.time() + 50_000)
    check("a stored time in the future wins over the system clock", c2.now() > time.time() + 40_000)

    c3 = clock.Clock()
    c3.logical = 1_000_000.0
    c3._boot -= 3  # three seconds since the last tick
    check("now() keeps moving between ticks", 1_000_002.5 < c3.now() < 1_000_004.0,
          f"got {c3.now()}")


# --- private files -----------------------------------------------------

def test_paths():
    from screen_time import paths

    section("private files")
    base = tempfile.mkdtemp()
    try:
        state = paths.private_dir(os.path.join(base, "state"))
        check("state directory is 0700", oct(os.stat(state).st_mode)[-3:] == "700")

        victim = os.path.join(base, "victim")
        with open(victim, "w") as handle:
            handle.write("must survive")
        planted = os.path.join(state, "cache.json")
        os.symlink(victim, planted)
        paths.write_private(planted, '{"hijacked": true}')
        with open(victim) as handle:
            check("a planted symlink does not redirect the write", handle.read() == "must survive")

        os.symlink(victim, os.path.join(state, "sneak.json"))
        paths.private_dir(state)
        check("the repair removes planted symlinks", not os.path.exists(os.path.join(state, "sneak.json")))

        loose = os.path.join(state, "loose.json")
        with open(loose, "w") as handle:
            handle.write("{}")
        os.chmod(loose, 0o644)
        paths.private_dir(state)
        check("the repair chmods files it finds", oct(os.stat(loose).st_mode)[-3:] == "600")

        os.symlink(victim, os.path.join(base, "link.json"))
        check("read_regular refuses a symlink", paths.read_regular(os.path.join(base, "link.json")) is None)
    finally:
        shutil.rmtree(base, ignore_errors=True)


# --- config ------------------------------------------------------------

def test_config():
    from screen_time import config

    section("config")
    cfg = config.sanitize({
        "profiles": {"kid": {"budget_minutes": {"mon": -5, "tue": "x", "wed": 99999},
                             "warn_minutes": ["a", -1, 5],
                             "on_empty": "rm -rf",
                             "earn": {"tables": [7, 999, "a"], "seconds_per_correct": 1e9}}},
        "active_profile": "does-not-exist",
    })
    profile = cfg["profiles"]["kid"]
    check("a negative budget becomes zero", profile["budget_minutes"]["mon"] == 0)
    check("a non-number budget falls back", profile["budget_minutes"]["tue"] == 60)
    check("a huge budget is clamped to a day", profile["budget_minutes"]["wed"] == 1440)
    check("junk warnings are dropped", profile["warn_minutes"] == [5])
    check("an unknown on_empty falls back to lock", profile["on_empty"] == "lock")
    check("impossible tables are dropped", profile["earn"]["tables"] == [7])
    check("the reward is clamped", profile["earn"]["seconds_per_correct"] == 600)
    check("an unknown active profile is corrected", cfg["active_profile"] == "kid")

    kohn = config.sanitize_profile({"philosophy": "together", "agreement_minutes": 90,
                                    "agreement_text": "x" * 900, "break_nudge_minutes": -5})
    check("together is a known philosophy", kohn["philosophy"] == "together")
    check("an unknown philosophy falls back to limits",
          config.sanitize_profile({"philosophy": "laissez-faire"})["philosophy"] == "limits")
    check("the agreement text is capped", len(kohn["agreement_text"]) == 500)
    check("a negative nudge becomes zero", kohn["break_nudge_minutes"] == 0)

    merged = config.deep_merge(
        {"earn": {"enabled": True, "tables": [1, 2]}, "grace_seconds": 60},
        {"earn": {"enabled": False}})
    check("a patch only touches what it names",
          merged["earn"]["enabled"] is False and merged["earn"]["tables"] == [1, 2]
          and merged["grace_seconds"] == 60)

    stored = config.hash_pin("4321")
    check("the right pin verifies", config.verify_pin(stored, "4321"))
    check("the wrong pin does not", not config.verify_pin(stored, "4322"))
    check("no pin means no access", not config.verify_pin(None, ""))
    check("the pin itself is not stored", "4321" not in json.dumps(stored))


# --- quiz --------------------------------------------------------------

def test_quiz():
    import random
    from screen_time import config, quiz

    section("quiz")
    earn = config.sanitize_earn({"ops": ["mul", "div"], "tables": [6, 7], "min_answer_seconds": 1.5})
    q = quiz.Quiz(earn, rng=random.Random(7))

    question = q.next_question(now=1000.0)
    check("division stays whole", question.op != "div" or question.left % question.right == 0)
    check("the answer is not in what the client gets",
          "answer" not in json.dumps(question.public(30, 90)))
    check("guessing instantly is refused",
          q.answer(question.id, question.answer, now=1000.4)["error"] == "too_fast")
    check("a stale question expires",
          q.answer(question.id, question.answer, now=1000.0 + 91)["error"] == "expired")

    question = q.next_question(now=2000.0)
    check("a right answer counts", q.answer(question.id, question.answer, now=2005.0)["correct"])
    question = q.next_question(now=3000.0)
    check("the same question cannot be answered twice",
          q.answer(question.id, question.answer, now=3005.0)["ok"]
          and not q.answer(question.id, question.answer, now=3006.0)["ok"])

    q.stats = {"mul:7:8": {"seen": 10, "wrong": 9, "last_wrong": time.time()},
               "mul:6:2": {"seen": 10, "wrong": 0}}
    q.config = config.sanitize_earn({"ops": ["mul"], "tables": [6, 7], "drill_weak": True})
    seen = {}
    for i in range(600):
        item = q.next_question(now=4000.0 + i)
        seen[item.key] = seen.get(item.key, 0) + 1
    check("a table that goes wrong comes back more often",
          seen.get("mul:7:8", 0) > seen.get("mul:6:2", 0) * 2,
          f"7x8={seen.get('mul:7:8')} 6x2={seen.get('mul:6:2')}")


# --- day state ---------------------------------------------------------

def test_state():
    from screen_time import paths, state

    section("ledger")
    base = tempfile.mkdtemp()
    try:
        os.environ["SCREEN_TIME_ROOT"] = base
        layout = paths.detect()
        store = state.Store(layout, os.getuid())
        today = state.day_key(time.time())
        day = store.load_day(today, 3600, "kid")
        day.spend(600)
        day.add("earn", 60, {"q": "7 x 8"})
        day.add("grant", 900)
        store.save_day(day)

        again = store.load_day(today, 0, "kid")
        check("the day survives a restart", again.remaining == 3600 + 60 + 900 - 600)
        check("the budget is not re-read from the profile", again.budget == 3600)

        for _ in range(400):
            day.record("noise")
        check("the ledger is capped", len(day.ledger) <= 200)

        yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        old = store.load_day(yesterday, 3600, "kid")
        old.spend(120)
        store.save_day(old)
        check("history returns newest first", [d["day"] for d in store.history(5)] == [today, yesterday])
    finally:
        os.environ.pop("SCREEN_TIME_ROOT", None)
        shutil.rmtree(base, ignore_errors=True)


# --- blocked periods and enforcement decisions -------------------------

def test_blocked_periods():
    from screen_time import config, daemon

    section("blocked periods")

    class Fake:
        _covers = staticmethod(daemon.Account._covers)
        blocking_period = daemon.Account.blocking_period
        next_period = daemon.Account.next_period

    def fake_with(periods):
        fake = Fake()
        fake.profile = {"blocked_periods": periods}
        return fake

    def one(start, end, enabled=True):
        return [{"label": "Bedtime", "enabled": enabled, "start": start, "end": end}]

    def at(hour, minute, start, end):
        moment = datetime(2026, 9, 2, hour, minute).timestamp()
        return fake_with(one(start, end)).blocking_period(moment) is not None

    check("before bedtime is allowed", not at(19, 59, "20:00", "07:00"))
    check("bedtime blocks the evening", at(20, 0, "20:00", "07:00"))
    check("bedtime blocks past midnight", at(2, 0, "20:00", "07:00"))
    check("morning is allowed again", not at(7, 0, "20:00", "07:00"))
    check("a window inside one day works", at(13, 30, "13:00", "15:00"))
    check("outside that window is allowed", not at(15, 30, "13:00", "15:00"))

    moment = datetime(2026, 9, 2, 21, 0).timestamp()
    check("a disabled period blocks nothing",
          fake_with(one("20:00", "07:00", enabled=False)).blocking_period(moment) is None)

    several = [
        {"label": "School", "enabled": True, "start": "08:30", "end": "15:00"},
        {"label": "Dinner", "enabled": True, "start": "18:00", "end": "18:45"},
        {"label": "Bedtime", "enabled": True, "start": "20:00", "end": "07:00"},
    ]

    def which(hour, minute):
        moment = datetime(2026, 9, 2, hour, minute).timestamp()
        found = fake_with(several).blocking_period(moment)
        return found["label"] if found else None

    check("school hours block the morning", which(9, 0) == "School")
    check("the gap after school is free", which(16, 0) is None)
    check("dinner blocks its own window", which(18, 30) == "Dinner")
    check("bedtime still blocks the night", which(3, 0) == "Bedtime")

    def upcoming(hour, minute):
        moment = datetime(2026, 9, 2, hour, minute).timestamp()
        return fake_with(several).next_period(moment)["label"]

    check("the next period is the one after now", upcoming(16, 0) == "Dinner")
    check("after the last one it wraps to tomorrow", upcoming(21, 0) == "School")

    # the config side: an old profile keeps its evening
    migrated = config.sanitize_profile({"bedtime": {"enabled": True, "start": "21:00", "end": "06:30"}})
    check("an old bedtime migrates into the list",
          migrated["blocked_periods"] == [{"label": "Bedtime", "enabled": True,
                                           "start": "21:00", "end": "06:30"}])
    empty_window = config.sanitize_profile({"blocked_periods": [
        {"label": "Nope", "enabled": True, "start": "10:00", "end": "10:00"}]})
    check("a window with no width is dropped", empty_window["blocked_periods"] == [])



# --- the idle flag is a hint, not a switch ------------------------------

def test_idle_window():
    from screen_time import daemon, paths, state

    section("the idle window")

    class Fake:
        idle_room = daemon.Account.idle_room
        claim_idle = daemon.Account.claim_idle
        age_idle = daemon.Account.age_idle
        username = "kid"
        log = staticmethod(lambda *a: None)
        save = staticmethod(lambda: None)

    base = tempfile.mkdtemp()
    try:
        os.environ["SCREEN_TIME_ROOT"] = base
        layout = paths.detect()
        store = state.Store(layout, os.getuid())

        def fresh():
            fake = Fake()
            fake.day = store.load_day("2026-09-05", 3600, "kid")
            fake.idle_since = None
            return fake

        now = time.time()

        fake = fresh()
        fake.claim_idle(True, now)
        check("a claim opens the window", fake.idle_since == now)

        # The bypass: repeating the claim used to push the expiry ahead of
        # itself, so the window never closed and the clock never ran.
        fake.claim_idle(True, now + 600)
        check("repeating the claim does not refresh it", fake.idle_since == now)

        fake.age_idle(now + daemon.IDLE_MAX_SECONDS + 1, 5, False)
        check("the window runs out on its own", fake.idle_since is None)

        # An open window costs the day's allowance, but only while the clock
        # would otherwise be running.
        fake = fresh()
        fake.claim_idle(True, now)
        fake.age_idle(now + 5, 5, False)
        check("being really away costs nothing", fake.day.idle_seconds == 0)
        fake.age_idle(now + 10, 5, True)
        check("holding the clock back costs the allowance", fake.day.idle_seconds == 5)

        fake = fresh()
        fake.day.idle_seconds = daemon.IDLE_CREDIT_SECONDS
        answer = fake.claim_idle(True, now)
        check("a spent allowance refuses the claim", fake.idle_since is None and not answer["idle"])
        check("the refusal is in the ledger",
              any(e["kind"] == "idle_refused" for e in fake.day.ledger))

        fake = fresh()
        fake.claim_idle(True, now)
        fake.day.idle_seconds = daemon.IDLE_CREDIT_SECONDS
        fake.age_idle(now + 5, 5, True)
        check("an open window closes once the allowance is gone", fake.idle_since is None)

        fake = fresh()
        fake.claim_idle(True, now)
        fake.claim_idle(False, now + 5)
        check("the session can still say it is back", fake.idle_since is None)
    finally:
        os.environ.pop("SCREEN_TIME_ROOT", None)
        shutil.rmtree(base, ignore_errors=True)


# --- the demo switch is a parent's, both ways --------------------------

def test_demo_gate():
    from screen_time import config, daemon

    section("the demo switch")

    class FakeDaemon:
        check_pin = daemon.Daemon.check_pin

        def __init__(self, config):
            self.config = config

    class FakeAccount:
        def __init__(self, together=False):
            self.together = together
            self.pin_failures = 0
            self.pin_blocked_until = 0.0

    no_pin = FakeDaemon({})
    kid = FakeAccount()

    def refusal(daemon_, account, command, **message):
        message["cmd"] = command
        return daemon_.check_pin(account, message, command)

    # The bypass: `demo on` stopped every tick before it counted and before it
    # enforced, and it went through on a machine with no PIN.
    check("demo on needs a PIN", refusal(no_pin, kid, "demo", value=True) is not None)
    check("demo off does not", refusal(no_pin, kid, "demo", value=False) is None)
    check("the first PIN can still be set", refusal(no_pin, kid, "pin.set") is None)
    check("a grant still needs a PIN", refusal(no_pin, kid, "grant", minutes=10) is not None)

    # Agreement mode waives the PIN on its own account's settings, but the demo
    # silences the daemon for every account, so it is not part of that.
    together = FakeAccount(together=True)
    check("agreement mode waives the PIN on its own settings",
          refusal(no_pin, together, "config.patch") is None)
    check("agreement mode does not waive it on demo on",
          refusal(no_pin, together, "demo", value=True) is not None)

    # With a PIN configured, both directions go through the real check.
    with_pin = FakeDaemon({"pin": config.hash_pin("4321")})
    check("a wrong PIN is refused", refusal(with_pin, kid, "demo", value=True, pin="1111") is not None)
    check("the right PIN is accepted", refusal(with_pin, kid, "demo", value=True, pin="4321") is None)



def test_strict_mode():
    """The three things the security review found in the root service."""
    from screen_time import daemon, paths

    section("strict mode")

    root = pathlib.Path(tempfile.mkdtemp())
    try:
        # A layout that says system without needing a system to run on.
        layout = paths.Layout("system", root / "config.json",
                              root / "state", root / "run" / "sock")

        # 1. The account directory stays root's. The bug was that the store
        # asked for the child as owner: on a fresh install systemd makes that
        # directory as root, so the store raised and the daemon restarted, and
        # repairing it the other way let the child edit its own spent time.
        src = pathlib.Path("screen_time/daemon.py").read_text()
        marker = "account = Account(self.layout, uid, self.config, owner_uid=None"
        check("the account store is never owned by the child", marker in src)

        # A directory owned by whoever is running this, which stands in for
        # root owning it on a real install, has to be accepted rather than
        # refused.
        state_dir = layout.user_state_dir(1000)
        paths.private_dir(state_dir)
        check("a fresh directory of our own is accepted",
              state_dir.is_dir() and (state_dir.stat().st_mode & 0o777) == 0o700)
        paths.private_dir(state_dir)
        check("and an existing one is accepted again", state_dir.is_dir())

        # 2. Idle is refused in system mode. It pauses the clock, and in this
        # mode the session it would come from belongs to the child. Asked of
        # the real dispatch rather than of the source text.
        d = daemon.Daemon.__new__(daemon.Daemon)
        d.layout = layout
        d.lock = threading.RLock()
        d.config = {}
        d.clock = type("C", (), {"now": staticmethod(lambda: 0.0)})()
        # A managed account, because an unmanaged one is refused a step earlier
        # and would prove nothing: the case that matters is the child this mode
        # is watching asking for its clock to stop.
        fake_account = type("A", (), {"claim_idle": staticmethod(
            lambda *a: {"ok": True, "idle": True})})()
        d.accounts = {1000: fake_account}
        answer = daemon.Daemon.dispatch(d, 1000, {"cmd": "idle", "value": True})
        check("system mode refuses a child idle claim",
              answer.get("ok") is False and answer.get("error") == "not_permitted",
              repr(answer))

        # 3. Connections are counted and capped, and the peer is looked at
        # before a thread exists, not inside one.
        check("there is a connection ceiling",
              "MAX_CONNECTIONS" in src and "MAX_CONNECTIONS_PER_UID" in src)
        check("the peer is admitted before a thread is started",
              src.index("if not self.admit(conn):")
              < src.index("threading.Thread(target=self.handle"))
        check("watch is refused for an unmanaged account",
              "if self.account_for(uid) is None:" in src)

        # The ceiling actually refuses once it is reached.
        d.conn_lock = threading.Lock()
        d.conn_total = daemon.MAX_CONNECTIONS
        d.conn_per_uid = {}
        d.managed_uids = lambda: [1000]
        class FakeConn:
            pass
        import screen_time.daemon as dmod
        real_peer = dmod.proto_peer_uid
        dmod.proto_peer_uid = staticmethod(lambda conn: 1000)
        try:
            check("a full daemon refuses another connection",
                  daemon.Daemon.admit(d, FakeConn()) is False)
            d.conn_total = 0
            d.conn_per_uid = {1000: daemon.MAX_CONNECTIONS_PER_UID}
            check("and refuses a uid that already holds its share",
                  daemon.Daemon.admit(d, FakeConn()) is False)
            d.conn_per_uid = {}
            check("but takes the next one when there is room",
                  daemon.Daemon.admit(d, FakeConn()) is True)
        finally:
            dmod.proto_peer_uid = real_peer

        # The socket is not world writable any more.
        check("the system socket is not 0666", "0o666" not in src)
        check("and it is group readable instead", "0o660" in src)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    test_clock()
    test_paths()
    test_config()
    test_quiz()
    test_state()
    test_blocked_periods()
    test_idle_window()
    test_demo_gate()
    test_strict_mode()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        return 1
    print("all good")
    return 0


# `python3 tests/test_core.py` is the intended way to run this, but it is not
# the way somebody trying the suite for the first time reaches for. Both of the
# obvious alternatives used to mislead: `unittest discover` found no TestCase
# and printed "NO TESTS RAN" while exiting 0, which reads as a green run rather
# than as a suite that never executed, and pytest is not always installed. This
# wrapper gives all three the same answer.
class ScreenTimeCoreTests(unittest.TestCase):
    def test_core(self):
        FAILURES.clear()
        self.assertEqual(main(), 0, "; ".join(FAILURES))


if __name__ == "__main__":
    raise SystemExit(main())
