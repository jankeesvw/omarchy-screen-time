"""The day's ledger and how it is stored.

One file per day, holding aggregates plus a ledger of the events that are worth
explaining to a child later: minutes earned, minutes given by a parent, the
moment the screen went on the lock. Spent time is an aggregate and not a ledger
entry, or the file would grow by a line every five seconds.
"""

import json
import time
from datetime import date, datetime

from . import paths

LEDGER_LIMIT = 200
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def day_key(timestamp):
    return date.fromtimestamp(timestamp).isoformat()


def weekday_key(timestamp):
    return DAY_KEYS[date.fromtimestamp(timestamp).weekday()]


class DayState:
    def __init__(self, day, budget_seconds, profile_key, raw=None):
        raw = raw if isinstance(raw, dict) else {}
        self.day = day
        self.profile = raw.get("profile", profile_key)
        self.budget = int(raw.get("budget_seconds", budget_seconds))
        self.spent = max(0, int(raw.get("spent_seconds", 0)))
        self.earned = max(0, int(raw.get("earned_seconds", 0)))
        self.granted = int(raw.get("granted_seconds", 0))
        self.correct = max(0, int(raw.get("correct_answers", 0)))
        self.warned = [int(w) for w in raw.get("warned", []) if isinstance(w, (int, float))]
        self.agreement_noted = bool(raw.get("agreement_noted", False))
        self.ledger = raw.get("ledger") if isinstance(raw.get("ledger"), list) else []
        self.dirty = False

    @property
    def total(self):
        return self.budget + self.earned + self.granted

    @property
    def remaining(self):
        return self.total - self.spent

    def spend(self, seconds):
        if seconds <= 0:
            return
        self.spent += int(seconds)
        self.dirty = True

    def add(self, kind, seconds, meta=None):
        seconds = int(seconds)
        if kind == "earn":
            self.earned += seconds
        elif kind == "grant":
            self.granted += seconds
        self.record(kind, seconds=seconds, meta=meta)

    def record(self, kind, seconds=None, meta=None):
        entry = {"t": round(time.time(), 1), "kind": kind}
        if seconds is not None:
            entry["seconds"] = int(seconds)
        if meta:
            entry["meta"] = meta
        self.ledger.append(entry)
        del self.ledger[:-LEDGER_LIMIT]
        self.dirty = True

    def to_json(self):
        return {
            "day": self.day,
            "profile": self.profile,
            "budget_seconds": self.budget,
            "spent_seconds": self.spent,
            "earned_seconds": self.earned,
            "granted_seconds": self.granted,
            "correct_answers": self.correct,
            "warned": self.warned,
            "agreement_noted": self.agreement_noted,
            "ledger": self.ledger,
        }

    def summary(self):
        return {
            "day": self.day,
            "budget_seconds": self.budget,
            "spent_seconds": self.spent,
            "earned_seconds": self.earned,
            "granted_seconds": self.granted,
            "correct_answers": self.correct,
        }


class Store:
    """Everything one account writes to disk."""

    def __init__(self, layout, uid, owner_uid=None):
        self.layout = layout
        self.uid = uid
        self.owner_uid = owner_uid
        self.root = layout.user_state_dir(uid)
        self.days_dir = self.root / "days"
        paths.private_dir(self.root, owner_uid=owner_uid)
        paths.private_dir(self.days_dir, owner_uid=owner_uid, scrub=False)

    def _read_json(self, path, fallback):
        text = paths.read_regular(path)
        if not text:
            return fallback
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            return fallback
        return value if isinstance(value, type(fallback)) else fallback

    def _write_json(self, path, value):
        paths.write_private(path, json.dumps(value, indent=2) + "\n", owner_uid=self.owner_uid)

    def load_day(self, day, budget_seconds, profile_key):
        raw = self._read_json(self.days_dir / f"{day}.json", {})
        return DayState(day, budget_seconds, profile_key, raw)

    def save_day(self, state):
        self._write_json(self.days_dir / f"{state.day}.json", state.to_json())
        state.dirty = False

    def load_meta(self):
        return self._read_json(self.root / "meta.json", {})

    def save_meta(self, meta):
        self._write_json(self.root / "meta.json", meta)

    def load_stats(self):
        return self._read_json(self.root / "stats.json", {})

    def save_stats(self, stats):
        self._write_json(self.root / "stats.json", stats)

    def history(self, days=14):
        out = []
        for path in sorted(self.days_dir.glob("*.json"), reverse=True)[:days]:
            raw = self._read_json(path, {})
            if raw.get("day"):
                out.append(DayState(raw["day"], 0, raw.get("profile", ""), raw).summary())
        return out
