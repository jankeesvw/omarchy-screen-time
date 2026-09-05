"""The daemon: counts the time, warns, and locks the screen.

It owns every file. Clients ask it questions over the socket and never touch
the state themselves, which is what lets the same code run as the child's own
user service and as a root service the child cannot stop.
"""

import json
import math
import os
import pwd
import signal
import socket
import threading
import time
from datetime import datetime

from . import clock, config as config_mod, paths, quiz as quiz_mod, session, state as state_mod

TICK_SECONDS = 5
SAVE_EVERY = 30
IDLE_MAX_SECONDS = 15 * 60      # how long one idle window may hold time back
IDLE_CREDIT_SECONDS = 60 * 60   # and how much of a day all of them together may
REST_RESET_SECONDS = 300  # five quiet minutes and a stretch starts over
REFLECTION_LIMIT = 20
PIN_LOCKOUT = [0, 0, 1, 5, 15, 60, 300]


def _human_time(seconds):
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        hours, minutes = seconds // 3600, (seconds % 3600) // 60
        # A whole hour reads as "1h"; the zeroes only earn their place when
        # there are minutes next to them.
        return "%dh" % hours if minutes == 0 else "%dh%02d" % (hours, minutes)
    return "%dm" % (seconds // 60)


def _bind(server, socket_path):
    """Bind, working around the 108 byte limit on unix socket paths.

    A long home directory or a test root under /tmp is enough to hit it, and
    the error ("AF_UNIX path too long") does not say which of your paths is at
    fault. Binding from inside the directory keeps the name short.
    """
    try:
        server.bind(str(socket_path))
        return
    except OSError:
        pass
    directory = os.open(str(socket_path.parent), os.O_RDONLY | os.O_DIRECTORY)
    previous = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fchdir(directory)
        server.bind(socket_path.name)
    finally:
        os.fchdir(previous)
        os.close(directory)
        os.close(previous)


class Account:
    """One child account: its budget, its ledger, its screen."""

    def __init__(self, layout, uid, config, owner_uid=None, log=print):
        self.layout = layout
        self.uid = int(uid)
        self.username = session.username_for(uid)
        self.log = log
        self.store = state_mod.Store(layout, self.uid, owner_uid=owner_uid)
        self.watcher = session.SessionWatcher(self.uid)
        self.stats = self.store.load_stats()
        self.meta = self.store.load_meta()

        self.profile_key, self.profile = config_mod.profile_for(config, self.username)
        self.quiz = quiz_mod.Quiz(self.profile["earn"], self.stats)

        self.paused = False
        self.idle_since = None
        self.stretch = 0.0        # unbroken screen time, for the break nudge
        self.rest_since = None
        self.nudged = False
        self.lock_after = None
        self.lock_count = 0
        self.last_lock_ok = False
        self.blocked_since = None
        self.last_save = 0.0
        self.pin_failures = 0
        self.pin_blocked_until = 0.0

        self.day = None
        self.rollover(time.time())

    # day handling -------------------------------------------------------

    def budget_for(self, now):
        return self.profile["budget_minutes"][state_mod.weekday_key(now)] * 60

    def rollover(self, now):
        key = state_mod.day_key(now)
        if self.day is not None and self.day.day == key:
            return False
        if self.day is not None and self.day.dirty:
            self.store.save_day(self.day)
        self.day = self.store.load_day(key, self.budget_for(now), self.profile_key)
        self.lock_after = None
        self.lock_count = 0
        self.blocked_since = None
        return True

    def apply_config(self, config):
        self.profile_key, self.profile = config_mod.profile_for(config, self.username)
        self.quiz.config = self.profile["earn"]
        if self.day is not None:
            wanted = self.budget_for(time.time())
            if self.day.budget != wanted:
                self.day.budget = wanted
                self.day.dirty = True

    # gates --------------------------------------------------------------

    @staticmethod
    def _covers(period, moment):
        start, end = period["start"], period["end"]
        if start == end:
            return False
        if start < end:
            return start <= moment < end
        # Wraps past midnight, which is the normal shape for a bedtime.
        return moment >= start or moment < end

    def blocking_period(self, now):
        """The enabled period covering this moment, or None.

        Returns the period itself rather than a bool, because the panel says
        which one it is: "dinner" and "bedtime" are not the same sentence.
        """
        moment = datetime.fromtimestamp(now).strftime("%H:%M")
        for period in self.profile["blocked_periods"]:
            if period["enabled"] and self._covers(period, moment):
                return period
        return None

    def next_period(self, now):
        """The enabled period that starts next, for the line under the bar."""
        moment = datetime.fromtimestamp(now).strftime("%H:%M")
        upcoming = [p for p in self.profile["blocked_periods"] if p["enabled"]]
        if not upcoming:
            return None
        later = [p for p in upcoming if p["start"] > moment]
        if later:
            return min(later, key=lambda p: p["start"])
        # Nothing left today, so the first one tomorrow.
        return min(upcoming, key=lambda p: p["start"])

    @property
    def together(self):
        return self.profile["philosophy"] == "together"

    def block_reason(self, now):
        if self.together:
            return None   # nothing blocks: the agreement is a conversation, not a gate
        if self.blocking_period(now) is not None:
            return "bedtime"
        if self.day.remaining <= 0:
            return "empty"
        return None

    @property
    def in_use(self):
        if self.idle_since is not None:
            return False
        return self.watcher.in_use

    # the tick -----------------------------------------------------------

    def tick(self, now, elapsed, demo=False):
        self.rollover(now)
        self.watcher.poll()

        if demo:
            return

        step = min(elapsed, TICK_SECONDS * 4)
        # Everything except the idle flag: whether the clock would be running
        # if nobody had claimed to be away.
        counting = (self.watcher.in_use and not self.paused
                    and self.block_reason(now) is None)
        self.age_idle(now, step, counting)

        if counting and self.idle_since is None:
            self.day.spend(step)
            self.stretch += step
            self.rest_since = None
            if self.together:
                self.nudge(now)
            else:
                self.warn(now)
        else:
            if self.rest_since is None:
                self.rest_since = now
            elif now - self.rest_since >= REST_RESET_SECONDS:
                self.stretch = 0.0
                self.nudged = False

        self.enforce(now)

        if self.day.dirty and now - self.last_save > SAVE_EVERY:
            self.save()

    # the idle window ----------------------------------------------------

    @property
    def idle_room(self):
        """Seconds of idle the flag may still hold back today."""
        return max(0, IDLE_CREDIT_SECONDS - self.day.idle_seconds)

    def claim_idle(self, value, now):
        """Take the session's word for being away, within a day's allowance.

        The flag arrives from the child's own session, because hypridle there
        runs the client. In strict mode there is nothing to authenticate that
        with: anything that reaches the socket can send it, the child included.
        So it is a hint and never a switch. One claim opens one window, a
        second claim does not refresh it, the window is short, and what all of
        them together hold back in a day is capped. Past that the time counts
        on however often the flag arrives.
        """
        if not value:
            self.idle_since = None
            return {"ok": True, "idle": False, "idle_seconds_left": self.idle_room}
        if self.idle_since is not None:
            # Already open. Saying it again buys nothing, which is the point:
            # a client that repeats the claim used to push the expiry ahead of
            # itself and the window never closed.
            return {"ok": True, "idle": True, "idle_seconds_left": self.idle_room}
        if self.idle_room <= 0:
            # Refused, and said in the ledger once. Once, because a client that
            # keeps asking must not be able to fill a day's ledger or make the
            # daemon write to disk on demand.
            if not any(entry.get("kind") == "idle_refused" for entry in self.day.ledger):
                self.day.record("idle_refused")
                self.save()
            return {"ok": True, "idle": False, "idle_seconds_left": 0}
        self.idle_since = now
        return {"ok": True, "idle": True, "idle_seconds_left": self.idle_room}

    def age_idle(self, now, step, counting):
        """Charge an open idle window to the day, and close it when it is due.

        Charged only while the clock would otherwise be running: a child who
        really did walk away has a locked or inactive session, and that stops
        the time by itself without spending any of the allowance.
        """
        if self.idle_since is None:
            return
        if counting:
            self.day.idle_seconds += int(step)
            self.day.dirty = True
        if now - self.idle_since > IDLE_MAX_SECONDS:
            self.log(f"idle window for {self.username} ran out, counting again")
            self.idle_since = None
        elif self.idle_room <= 0:
            self.log(f"idle allowance for {self.username} is spent, counting again")
            self.idle_since = None

    def nudge(self, now):
        """The together mode's whole voice: information, never a threat.

        One nudge per unbroken stretch, and one note per day when the time
        passes what the family agreed on. Both are plain statements; nothing
        counts down and nothing follows if they are ignored.
        """
        minutes = self.profile["break_nudge_minutes"]
        if minutes > 0 and not self.nudged and self.stretch >= minutes * 60:
            self.nudged = True
            session.notify(self.uid, "Screen Time",
                           "You have been at it for %d minutes straight. A little break?" % minutes,
                           tag="nudge")
        agreement = self.profile["agreement_minutes"]
        if agreement > 0 and not self.day.agreement_noted and self.day.spent >= agreement * 60:
            self.day.agreement_noted = True
            self.day.dirty = True
            session.notify(self.uid, "Screen Time",
                           "Your agreement is about %s of screen time. You are at %s now."
                           % (_human_time(agreement * 60), _human_time(self.day.spent)),
                           tag="agreement")

    def reflections(self):
        out = []
        for entry in self.day.ledger:
            if entry.get("kind") != "reflection":
                continue
            meta = entry.get("meta") or {}
            out.append({"t": entry.get("t", 0), "text": str(meta.get("text", ""))})
        return out[-REFLECTION_LIMIT:]

    def warn(self, now):
        left = self.day.remaining
        for threshold in self.profile["warn_minutes"]:
            if threshold in self.day.warned:
                continue
            if left <= threshold * 60:
                self.day.warned.append(threshold)
                self.day.dirty = True
                body = ("%d minute left." if threshold == 1 else "%d minutes left.") % threshold
                if self.profile["earn"]["enabled"] and self.earn_room() > 0:
                    body += " Earn more with math problems."
                session.notify(self.uid, "Screen Time", body,
                               urgency="critical" if threshold <= 5 else "normal", tag="warn")
                break

    def enforce(self, now):
        reason = self.block_reason(now)
        if reason is None or self.paused:
            if self.blocked_since is not None:
                self.clear_block()
            return

        if self.blocked_since is None:
            self.blocked_since = now
            self.day.record("blocked", meta={"reason": reason})
            self.save()
            if self.profile["on_empty"] == "notify":
                session.notify(self.uid, "Time's up",
                               self.blocked_headline(now, reason),
                               urgency="critical", tag="empty")

        if self.profile["on_empty"] != "lock":
            return
        if not self.watcher.present:
            return
        if self.watcher.locked:
            self.lock_after = None
            return

        delay, kind = self.lock_delay()
        if self.lock_after is None:
            self.lock_after = now + delay
            headline = self.blocked_headline(now, reason)
            if kind == "after_unlock":
                headline = "There is no time yet."
            session.notify(self.uid, "Time's up",
                           f"{headline} The screen locks in {int(delay)} seconds.",
                           urgency="critical", tag="empty")
        elif now >= self.lock_after:
            used = session.lock(self.uid, self.watcher.session_id)
            self.lock_count += 1
            self.last_lock_ok = bool(used)
            self.lock_after = None
            self.day.record("locked", meta={"reason": reason, "via": used or "failed"})
            self.save()
            self.log(f"locked {self.username} ({reason}) via {used}")

    def lock_delay(self):
        """How long before the screen goes on the lock, and why that long.

        The three cases are genuinely different. The first is the child being
        told to wrap up. A retry after a lock that did not take should come
        round quickly. But a session that is unlocked again while the budget is
        zero was unlocked by somebody holding the account password, and on a
        machine set up for a child that is the parent, so they get room to open
        the panel and hand out minutes instead of racing a countdown.
        """
        if self.lock_count == 0:
            return self.profile["grace_seconds"], "first"
        if not self.last_lock_ok:
            return self.profile["relock_seconds"], "retry"
        return self.profile["unlock_grace_seconds"], "after_unlock"

    def clear_block(self):
        """Time was added, so whatever was counting down to the lock stops now.

        The next tick would do this anyway, but a panel refreshes right after
        the click and would otherwise still show a lock coming.
        """
        self.blocked_since = None
        self.lock_after = None
        self.lock_count = 0
        self.last_lock_ok = False

    def save(self):
        self.store.save_day(self.day)
        self.store.save_stats(self.stats)
        self.meta["last_logical"] = time.time()
        self.store.save_meta(self.meta)
        self.last_save = time.time()

    # earning ------------------------------------------------------------

    def earn_room(self):
        cap = self.profile["earn"]["daily_cap_minutes"] * 60
        return max(0, cap - self.day.earned)

    def quiz_next(self, now):
        earn = self.profile["earn"]
        if self.together or not earn["enabled"]:
            return {"ok": False, "error": "earning_disabled"}
        if self.earn_room() <= 0:
            return {"ok": False, "error": "daily_cap_reached",
                    "cap_minutes": earn["daily_cap_minutes"]}
        question = self.quiz.next_question(now)
        if question is None:
            return {"ok": False, "error": "no_questions"}
        reward = min(earn["seconds_per_correct"], self.earn_room())
        return {"ok": True, "question": question.public(reward, earn["question_timeout_seconds"]),
                "earn_room_seconds": self.earn_room()}

    def quiz_answer(self, question_id, given, now):
        earn = self.profile["earn"]
        verdict = self.quiz.answer(question_id, given, now)
        if not verdict.get("ok"):
            return verdict
        reward = 0
        if verdict["correct"]:
            reward = min(earn["seconds_per_correct"], self.earn_room())
            if reward > 0:
                self.day.add("earn", reward, {"q": verdict["text"]})
                if self.day.remaining > 0:
                    self.clear_block()
            self.day.correct += 1
            self.day.dirty = True
        else:
            # A miss is worth keeping too. A list that only shows what went
            # right says nothing about which tables are still hard, and the
            # child gets to see their own afternoon rather than a scoreboard.
            self.day.record("miss", meta={"q": verdict.get("text", ""),
                                          "given": verdict.get("given"),
                                          "answer": verdict.get("answer")})
        self.store.save_stats(self.stats)
        self.save()
        verdict.update({
            "reward_seconds": reward,
            "earn_room_seconds": self.earn_room(),
            "remaining_seconds": max(0, self.day.remaining),
        })
        return verdict

    def blocked_headline(self, now, reason):
        """What to call the block in a notification, in the family's words."""
        if reason == "empty":
            return "Today's screen time is used up."
        period = self.blocking_period(now)
        label = period["label"].strip().lower() if period else "a quiet time"
        return f"It is {label}."

    # status -------------------------------------------------------------

    def status(self, now):
        reason = self.block_reason(now)
        if self.paused:
            phase = "paused"
        elif reason == "bedtime":
            phase = "bedtime"
        elif reason == "empty":
            phase = "empty"
        elif not self.in_use:
            phase = "idle"
        else:
            phase = "running"
        earn = self.profile["earn"]
        return {
            "ok": True,
            "user": self.username,
            "profile": self.profile_key,
            "profile_name": self.profile["name"],
            "philosophy": self.profile["philosophy"],
            "agreement_text": self.profile["agreement_text"],
            "agreement_minutes": self.profile["agreement_minutes"],
            "break_nudge_minutes": self.profile["break_nudge_minutes"],
            "stretch_seconds": int(self.stretch),
            "reflections": self.reflections(),
            "day": self.day.day,
            "phase": phase,
            "counting": phase == "running",
            "remaining_seconds": max(0, self.day.remaining),
            "budget_seconds": self.day.budget,
            "spent_seconds": self.day.spent,
            "earned_seconds": self.day.earned,
            "granted_seconds": self.day.granted,
            "correct_answers": self.day.correct,
            "warn_seconds": [m * 60 for m in self.profile["warn_minutes"]],
            "locked": self.watcher.locked,
            "session_present": self.watcher.present,
            "lock_in_seconds": (max(0, int(self.lock_after - now))
                                if self.lock_after and reason and not self.paused else None),
            "blocked_periods": self.profile["blocked_periods"],
            "blocked_label": (self.blocking_period(now) or {}).get("label", ""),
            "next_block": self.next_period(now),
            "budget_minutes": dict(self.profile["budget_minutes"]),
            "on_empty": self.profile["on_empty"],
            "earn": {
                "enabled": earn["enabled"],
                "seconds_per_correct": earn["seconds_per_correct"],
                "cap_seconds": earn["daily_cap_minutes"] * 60,
                "room_seconds": self.earn_room(),
                "ops": earn["ops"],
                "tables": earn["tables"],
                "events": self.earn_events(),
            },
        }

    def earn_events(self, limit=50):
        """Today's sums, oldest first, for the panel's tally list.

        Both the rewards and the misses, because the misses are the half that
        says which tables are still hard.
        """
        out = []
        for entry in self.day.ledger:
            kind = entry.get("kind")
            if kind not in ("earn", "miss"):
                continue
            meta = entry.get("meta") or {}
            row = {"t": entry.get("t", 0),
                   "kind": kind,
                   "seconds": int(entry.get("seconds", 0)),
                   "q": str(meta.get("q", ""))}
            if kind == "miss":
                row["given"] = meta.get("given")
                row["answer"] = meta.get("answer")
            out.append(row)
        return out[-limit:]


DEMO_STATUS = {
    "ok": True,
    "user": "sam",
    "profile": "sam",
    "profile_name": "Sam",
    "philosophy": "limits",
    "agreement_text": "",
    "agreement_minutes": 0,
    "break_nudge_minutes": 45,
    "stretch_seconds": 1455,
    "reflections": [],
    "pin_set": True,
    "day": "2026-09-02",
    "phase": "running",
    "counting": True,
    "remaining_seconds": 2745,
    "budget_seconds": 3600,
    "spent_seconds": 1455,
    "earned_seconds": 600,
    "granted_seconds": 0,
    "correct_answers": 20,
    "warn_seconds": [900, 300, 60],
    "locked": False,
    "session_present": True,
    "lock_in_seconds": None,
    "blocked_periods": [
        {"label": "School", "enabled": True, "start": "08:30", "end": "15:00"},
        {"label": "Dinner", "enabled": True, "start": "18:00", "end": "18:45"},
        {"label": "Bedtime", "enabled": True, "start": "20:00", "end": "07:00"},
    ],
    "blocked_label": "",
    "next_block": {"label": "Dinner", "enabled": True, "start": "18:00", "end": "18:45"},
    "on_empty": "lock",
    "earn": {
        "enabled": True,
        "seconds_per_correct": 30,
        "cap_seconds": 1800,
        "room_seconds": 1200,
        "ops": ["mul", "div"],
        "tables": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "events": [
            {"t": 1788470000.0, "kind": "earn", "seconds": 30, "q": "7 × 8"},
            {"t": 1788470100.0, "kind": "miss", "seconds": 0, "q": "8 × 7",
             "given": 54, "answer": 56},
            {"t": 1788470200.0, "kind": "earn", "seconds": 30, "q": "54 ÷ 6"},
            {"t": 1788470300.0, "kind": "earn", "seconds": 30, "q": "9 × 6"},
        ],
    },
    "demo": True,
}

# The PIN the demo answers to. The demo has no stored PIN of its own, and a
# gate that opens on anything is worse than no gate at all, so it gets one
# published number instead. It is written down in the README.
DEMO_PIN = "1234"

DEMO_HISTORY = [
    {"day": "2026-09-02", "budget_seconds": 3600, "spent_seconds": 1455, "earned_seconds": 600,
     "granted_seconds": 0, "correct_answers": 20},
    {"day": "2026-09-01", "budget_seconds": 3600, "spent_seconds": 4080, "earned_seconds": 480,
     "granted_seconds": 0, "correct_answers": 16},
    {"day": "2026-08-31", "budget_seconds": 5400, "spent_seconds": 5400, "earned_seconds": 900,
     "granted_seconds": 900, "correct_answers": 30},
    {"day": "2026-08-30", "budget_seconds": 5400, "spent_seconds": 3120, "earned_seconds": 0,
     "granted_seconds": 0, "correct_answers": 0},
    {"day": "2026-08-29", "budget_seconds": 3600, "spent_seconds": 3600, "earned_seconds": 300,
     "granted_seconds": 600, "correct_answers": 10},
]


class Daemon:
    def __init__(self, layout, tick_seconds=TICK_SECONDS, log=print):
        self.layout = layout
        self.tick_seconds = tick_seconds
        self.log = log
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.config = config_mod.load(layout)
        self.accounts = {}
        self.watchers = []

        self.clock = clock.Clock()
        self.server = None

    # accounts -----------------------------------------------------------

    def managed_uids(self):
        if self.layout.mode == "system":
            uids = []
            for name in self.config.get("users", {}):
                try:
                    uids.append(pwd.getpwnam(name).pw_uid)
                except KeyError:
                    self.log(f"config lists unknown account: {name}")
            return uids
        return [os.getuid()]

    def account_for(self, uid):
        with self.lock:
            if uid in self.accounts:
                return self.accounts[uid]
            if uid not in self.managed_uids():
                return None
            owner = uid if self.layout.mode == "system" else None
            account = Account(self.layout, uid, self.config, owner_uid=owner, log=self.log)
            floor = account.meta.get("last_logical")
            if floor and floor > self.clock.now():
                self.clock.logical = float(floor)
                self.log("stored time is ahead of the system clock, following the stored one")
            self.accounts[uid] = account
            return account

    # socket -------------------------------------------------------------

    def listen(self):
        socket_path = self.layout.socket_path
        if self.layout.mode == "system":
            self.layout.runtime_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.layout.runtime_dir, 0o755)
        else:
            paths.private_dir(self.layout.runtime_dir, scrub=False)
        if socket_path.exists() or socket_path.is_symlink():
            socket_path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        _bind(server, socket_path)
        os.chmod(socket_path, 0o666 if self.layout.mode == "system" else 0o600)
        server.listen(16)
        server.settimeout(1.0)
        self.server = server
        self.log(f"listening on {socket_path} ({self.layout.mode} mode)")

    def serve_forever(self):
        while not self.stop_event.is_set():
            try:
                conn, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn):
        try:
            conn.settimeout(30)
            uid = proto_peer_uid(conn)
            reader = proto_line_reader(conn)
            while True:
                message = reader.read()
                if message is None:
                    return
                if not isinstance(message, dict):
                    proto_write(conn, {"ok": False, "error": "bad_request"})
                    continue
                if message.get("cmd") == "watch":
                    self.stream(conn, uid)
                    return
                proto_write(conn, self.dispatch(uid, message))
        except Exception as exc:  # a broken client must never take the daemon down
            try:
                proto_write(conn, {"ok": False, "error": "internal", "detail": str(exc)[:200]})
            except OSError:
                pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def stream(self, conn, uid):
        last = None
        while not self.stop_event.is_set():
            with self.lock:
                account = self.account_for(uid)
                payload = self.status_for(account)
            if payload != last:
                proto_write(conn, payload)
                last = payload
            if self.stop_event.wait(1.0):
                return

    # commands -----------------------------------------------------------

    def check_pin(self, account, message, command=""):
        stored = self.config.get("pin")
        if not stored:
            # No PIN configured is not the same as no PIN required. Without
            # this the lock in the panel is decoration: every parent action
            # goes through on an empty config. Two things still have to work:
            # setting that first PIN, or nobody could ever start.
            if command == "pin.set":
                return None
            # Switching the demo off is the other one, or a machine without a
            # PIN could never leave demo mode. Switching it ON is not the same
            # favour: a tick in demo mode returns before it counts and before
            # it enforces, so an open `demo on` is a way to stop the clock.
            # Off without a PIN, on never.
            if command == "demo" and not message.get("value"):
                return None
            # Agreement mode has nothing to gate: no budget, no grants, and an
            # agreement that is meant to be written together in the first
            # place. A PIN there would only lock a family out of their own
            # words. A household that did set one keeps it, because the check
            # below still runs. The demo is not part of that: it silences the
            # daemon for every account, not only for this one.
            if account is not None and account.together and command != "demo":
                return None
            # The demo pretends to be a household that has a PIN, so the gate
            # is real there too and DEMO_PIN is the one that opens it. Letting
            # the demo through on anything would leave a drawer that opens on
            # a wrong PIN, which is the hole this check exists to close.
            if self.config.get("demo"):
                if str(message.get("pin", "")) == DEMO_PIN:
                    return None
                return {"ok": False, "error": "bad_pin"}
            return {"ok": False, "error": "no_pin_set"}
        now = time.time()
        if account and now < account.pin_blocked_until:
            return {"ok": False, "error": "pin_locked_out",
                    "retry_in_seconds": math.ceil(account.pin_blocked_until - now)}
        if config_mod.verify_pin(stored, message.get("pin", "")):
            if account:
                account.pin_failures = 0
            return None
        if account:
            account.pin_failures += 1
            index = min(account.pin_failures, len(PIN_LOCKOUT) - 1)
            account.pin_blocked_until = now + PIN_LOCKOUT[index]
        return {"ok": False, "error": "bad_pin"}

    def status_for(self, account):
        if self.config.get("demo"):
            return dict(DEMO_STATUS)
        if account is None:
            return {"ok": False, "error": "not_managed"}
        with self.lock:
            payload = account.status(self.clock.now())
            payload["pin_set"] = bool(self.config.get("pin"))
            return payload

    def dispatch(self, uid, message):
        command = str(message.get("cmd", ""))
        account = self.account_for(uid)
        now = self.clock.now()
        demo = bool(self.config.get("demo"))

        if command == "ping":
            return {"ok": True, "mode": self.layout.mode, "demo": demo}

        if command == "status":
            return self.status_for(account)

        if command == "history":
            if demo:
                return {"ok": True, "days": list(DEMO_HISTORY)}
            if account is None:
                return {"ok": False, "error": "not_managed"}
            days = message.get("days", 14)
            days = days if isinstance(days, int) and 1 <= days <= 366 else 14
            with self.lock:
                return {"ok": True, "days": account.store.history(days)}

        if account is None:
            return {"ok": False, "error": "not_managed"}

        if command == "quiz.next":
            if demo:
                return {"ok": True, "question": {"id": "demo", "text": "7 × 8", "op": "mul",
                                                 "reward_seconds": 30, "timeout_seconds": 90},
                        "earn_room_seconds": 1200}
            with self.lock:
                return account.quiz_next(now)

        if command == "quiz.answer":
            if demo:
                return {"ok": True, "correct": True, "text": "7 × 8", "answer": 56,
                        "given": 56, "seconds_taken": 3.4, "reward_seconds": 30,
                        "earn_room_seconds": 1170, "remaining_seconds": 2775}
            with self.lock:
                return account.quiz_answer(message.get("id"), message.get("answer"), now)

        if command == "idle":
            with self.lock:
                return account.claim_idle(message.get("value"), now)

        if command == "reflect":
            # The child's own words about their own time. No PIN: the journal
            # belongs to the child, the parent only sees what gets shown.
            text = str(message.get("text", "")).strip()[:280]
            if not text:
                return {"ok": False, "error": "empty"}
            with self.lock:
                account.day.record("reflection", meta={"text": text})
                account.save()
                return {"ok": True, "reflections": account.reflections()}

        if command == "reflect.forget":
            # Taking a note back is the child's too, so it asks for no PIN
            # either. Matched on the entry's own timestamp: the panel hands
            # back what it was given rather than an index into a list that
            # may have grown since.
            try:
                stamp = round(float(message.get("t", 0)), 1)
            except (TypeError, ValueError):
                return {"ok": False, "error": "bad_timestamp"}
            with self.lock:
                before = len(account.day.ledger)
                account.day.ledger = [
                    entry for entry in account.day.ledger
                    if not (entry.get("kind") == "reflection"
                            and round(float(entry.get("t", 0)), 1) == stamp)
                ]
                if len(account.day.ledger) == before:
                    return {"ok": False, "error": "no_such_note"}
                account.day.dirty = True
                account.save()
                return {"ok": True, "reflections": account.reflections()}

        # everything below changes the budget, so it is the parent's to do
        refusal = self.check_pin(account, message, command)
        if refusal:
            return refusal
        if demo and command in ("grant", "pause", "lock"):
            return {"ok": True, "demo": True, "note": "demo mode, nothing changed"}

        if command == "grant":
            minutes = message.get("minutes")
            if not isinstance(minutes, int) or not (-600 <= minutes <= 600):
                return {"ok": False, "error": "bad_minutes"}
            with self.lock:
                account.day.add("grant", minutes * 60, {"by": "parent"})
                if account.day.remaining > 0:
                    account.clear_block()
                if minutes > 0:
                    account.day.warned = [w for w in account.day.warned
                                          if w * 60 >= account.day.remaining]
                account.save()
                session.notify(account.uid, "Screen Time",
                               f"You got {minutes} extra minutes." if minutes > 0
                               else f"{abs(minutes)} minutes were taken away.", tag="grant")
                return account.status(now)

        if command == "pause":
            with self.lock:
                account.paused = bool(message.get("value", True))
                account.day.record("pause" if account.paused else "resume")
                account.save()
                return account.status(now)

        if command == "lock":
            with self.lock:
                used = session.lock(account.uid, account.watcher.session_id)
                account.day.record("locked", meta={"reason": "parent", "via": used or "failed"})
                account.save()
                return {"ok": bool(used), "via": used}

        if command == "config.get":
            with self.lock:
                safe = json.loads(json.dumps(self.config))
                safe["pin"] = bool(safe.get("pin"))
                return {"ok": True, "config": safe, "mode": self.layout.mode}

        if command == "config.patch":
            # A partial change to this account's own profile, so the settings
            # window can flip one switch without resending the whole config.
            patch = message.get("patch")
            if not isinstance(patch, dict):
                return {"ok": False, "error": "bad_patch"}
            with self.lock:
                key = account.profile_key
                current = json.loads(json.dumps(self.config["profiles"].get(key, {})))
                self.config["profiles"][key] = config_mod.sanitize_profile(
                    config_mod.deep_merge(current, patch))
                merged = config_mod.sanitize(self.config)
                merged["pin"] = self.config.get("pin")
                merged["demo"] = bool(self.config.get("demo"))
                self.config = merged
                config_mod.save(self.layout, merged)
                for existing in self.accounts.values():
                    existing.apply_config(merged)
                return {"ok": True, "profile": key}

        if command == "config.set":
            incoming = message.get("config")
            if not isinstance(incoming, dict):
                return {"ok": False, "error": "bad_config"}
            with self.lock:
                merged = config_mod.sanitize(incoming)
                merged["pin"] = self.config.get("pin")
                # Not incoming["demo"]. A config write must not be a second,
                # quieter way into demo mode; the `demo` command is the door
                # and check_pin is the lock on it.
                merged["demo"] = bool(self.config.get("demo"))
                self.config = merged
                config_mod.save(self.layout, merged)
                for existing in self.accounts.values():
                    existing.apply_config(merged)
                return {"ok": True, "config_applied": True}

        if command == "pin.set":
            new_pin = str(message.get("new_pin", ""))
            if not new_pin.isdigit() or not (4 <= len(new_pin) <= 12):
                return {"ok": False, "error": "pin_must_be_4_to_12_digits"}
            with self.lock:
                self.config["pin"] = config_mod.hash_pin(new_pin)
                config_mod.save(self.layout, self.config)
                return {"ok": True}

        if command == "demo":
            with self.lock:
                self.config["demo"] = bool(message.get("value"))
                config_mod.save(self.layout, self.config)
                return {"ok": True, "demo": self.config["demo"]}

        return {"ok": False, "error": "unknown_command", "cmd": command}

    # main loop ----------------------------------------------------------

    def run(self):
        self.listen()
        signal.signal(signal.SIGTERM, lambda *_: self.shutdown())
        signal.signal(signal.SIGINT, lambda *_: self.shutdown())
        threading.Thread(target=self.serve_forever, daemon=True).start()

        for uid in self.managed_uids():
            self.account_for(uid)

        while not self.stop_event.is_set():
            now, elapsed = self.clock.tick()
            demo = bool(self.config.get("demo"))
            with self.lock:
                for uid in list(self.managed_uids()):
                    account = self.account_for(uid)
                    if account:
                        try:
                            account.tick(now, elapsed, demo=demo)
                        except Exception as exc:
                            self.log(f"tick failed for uid {uid}: {exc}")
            if self.clock.last_jump:
                jump = self.clock.last_jump
                self.clock.last_jump = None
                self.log(f"system clock jumped by {jump['drift_seconds']}s, ignored")
                with self.lock:
                    for account in self.accounts.values():
                        account.day.record("clock_jump", meta=jump)
            self.stop_event.wait(self.tick_seconds)

        with self.lock:
            for account in self.accounts.values():
                account.save()
        self.log("stopped")

    def shutdown(self, *_):
        self.stop_event.set()
        if self.server:
            try:
                self.server.close()
            except OSError:
                pass


# small indirections so the socket helpers stay in one module
def proto_peer_uid(conn):
    from .proto import peer_uid
    return peer_uid(conn)


def proto_line_reader(conn):
    from .proto import LineReader
    return LineReader(conn)


def proto_write(conn, payload):
    from .proto import write_line
    return write_line(conn, payload)
