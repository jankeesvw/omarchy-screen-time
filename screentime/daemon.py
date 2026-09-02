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
IDLE_MAX_SECONDS = 4 * 3600
PIN_LOCKOUT = [0, 0, 1, 5, 15, 60, 300]


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

    def in_bedtime(self, now):
        bedtime = self.profile["bedtime"]
        if not bedtime["enabled"]:
            return False
        moment = datetime.fromtimestamp(now).strftime("%H:%M")
        start, end = bedtime["start"], bedtime["end"]
        if start == end:
            return False
        if start < end:
            return start <= moment < end
        return moment >= start or moment < end

    def block_reason(self, now):
        if self.in_bedtime(now):
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

        if self.idle_since is not None and now - self.idle_since > IDLE_MAX_SECONDS:
            self.log(f"idle flag for {self.username} expired, counting again")
            self.idle_since = None

        if demo:
            return

        if self.in_use and not self.paused and self.block_reason(now) is None:
            self.day.spend(min(elapsed, TICK_SECONDS * 4))
            self.warn(now)

        self.enforce(now)

        if self.day.dirty and now - self.last_save > SAVE_EVERY:
            self.save()

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
                session.notify(self.uid, "Screen time", body,
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
                               "Today's screen time is used up." if reason == "empty" else "It's bedtime.",
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
            headline = "Today's screen time is used up." if reason == "empty" else "It's bedtime."
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
        if not earn["enabled"]:
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
        self.store.save_stats(self.stats)
        self.save()
        verdict.update({
            "reward_seconds": reward,
            "earn_room_seconds": self.earn_room(),
            "remaining_seconds": max(0, self.day.remaining),
        })
        return verdict

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
            "bedtime": self.profile["bedtime"],
            "on_empty": self.profile["on_empty"],
            "earn": {
                "enabled": earn["enabled"],
                "seconds_per_correct": earn["seconds_per_correct"],
                "cap_seconds": earn["daily_cap_minutes"] * 60,
                "room_seconds": self.earn_room(),
                "ops": earn["ops"],
                "tables": earn["tables"],
            },
        }


DEMO_STATUS = {
    "ok": True,
    "user": "sam",
    "profile": "sam",
    "profile_name": "Sam",
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
    "bedtime": {"enabled": True, "start": "20:00", "end": "07:00"},
    "on_empty": "lock",
    "earn": {
        "enabled": True,
        "seconds_per_correct": 30,
        "cap_seconds": 1800,
        "room_seconds": 1200,
        "ops": ["mul", "div"],
        "tables": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    },
    "demo": True,
}

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

    def check_pin(self, account, message):
        stored = self.config.get("pin")
        if not stored:
            return None
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
            return account.status(self.clock.now())

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
                account.idle_since = now if message.get("value") else None
                return {"ok": True, "idle": account.idle_since is not None}

        # everything below changes the budget, so it is the parent's to do
        refusal = self.check_pin(account, message)
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
                session.notify(account.uid, "Screen time",
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

        if command == "config.set":
            incoming = message.get("config")
            if not isinstance(incoming, dict):
                return {"ok": False, "error": "bad_config"}
            with self.lock:
                merged = config_mod.sanitize(incoming)
                merged["pin"] = self.config.get("pin")
                merged["demo"] = bool(incoming.get("demo", self.config.get("demo")))
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
