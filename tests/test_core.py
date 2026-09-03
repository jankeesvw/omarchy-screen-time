#!/usr/bin/env python3
"""Tests that do not need a running daemon or a desktop.

Run with: python3 tests/test_core.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
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
                                    "agreement_text": "x" * 500, "break_nudge_minutes": -5})
    check("together is a known philosophy", kohn["philosophy"] == "together")
    check("an unknown philosophy falls back to limits",
          config.sanitize_profile({"philosophy": "laissez-faire"})["philosophy"] == "limits")
    check("the agreement text is capped", len(kohn["agreement_text"]) == 200)
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


# --- bedtime and enforcement decisions ---------------------------------

def test_bedtime():
    from screen_time import config, daemon

    section("bedtime")

    class Fake:
        in_bedtime = daemon.Account.in_bedtime

    def at(hour, minute, start, end):
        fake = Fake()
        fake.profile = {"bedtime": {"enabled": True, "start": start, "end": end}}
        moment = datetime(2026, 9, 2, hour, minute).timestamp()
        return fake.in_bedtime(moment)

    check("before bedtime is allowed", not at(19, 59, "20:00", "07:00"))
    check("bedtime blocks the evening", at(20, 0, "20:00", "07:00"))
    check("bedtime blocks past midnight", at(2, 0, "20:00", "07:00"))
    check("morning is allowed again", not at(7, 0, "20:00", "07:00"))
    check("a window inside one day works", at(13, 30, "13:00", "15:00"))
    check("outside that window is allowed", not at(15, 30, "13:00", "15:00"))


def main():
    test_clock()
    test_paths()
    test_config()
    test_quiz()
    test_state()
    test_bedtime()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
