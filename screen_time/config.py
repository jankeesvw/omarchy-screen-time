"""Configuration, profiles and the parent PIN.

Every value that reaches the daemon from disk is clamped here. A profile with
a negative budget or a string where a number belongs must not be able to crash
the daemon, because a daemon that is not running is unlimited screen time.
"""

import hashlib
import json
import os
import secrets

from . import paths

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
OPS = ["add", "sub", "mul", "div"]

PBKDF2_ITERATIONS = 600_000

DEFAULT_EARN = {
    "enabled": True,
    "seconds_per_correct": 30,
    "daily_cap_minutes": 30,
    "min_answer_seconds": 1.5,
    "question_timeout_seconds": 90,
    "ops": ["mul", "div"],
    "tables": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    "max_term": 100,
    "drill_weak": True,
}

DEFAULT_PROFILE = {
    "name": "Default",
    # "limits": budget, lock, and earning. "together": no lock and no rewards,
    # just a shared agreement, gentle information, and the child's own notes.
    "philosophy": "limits",
    "budget_minutes": {"mon": 60, "tue": 60, "wed": 60, "thu": 60, "fri": 60, "sat": 90, "sun": 90},
    # A day can be blocked more than once: school hours, dinner, bedtime.
    # Bedtime is just the one every family starts with.
    "blocked_periods": [
        {"label": "Bedtime", "enabled": False, "start": "20:00", "end": "07:00"},
    ],
    "warn_minutes": [15, 5, 1],
    "on_empty": "lock",
    "grace_seconds": 60,
    "relock_seconds": 30,
    "unlock_grace_seconds": 120,
    "agreement_text": "",
    "agreement_minutes": 0,
    "break_nudge_minutes": 45,
    "earn": dict(DEFAULT_EARN),
}

DEFAULT_CONFIG = {
    "version": 1,
    "pin": None,
    "active_profile": "default",
    "profiles": {"default": dict(DEFAULT_PROFILE)},
    "users": {},
    "demo": False,
}


def _int(value, fallback, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _float(value, fallback, low, high):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _time_of_day(value, fallback):
    text = str(value or "")
    parts = text.split(":")
    if len(parts) != 2:
        return fallback
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return fallback
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return fallback
    return f"{hour:02d}:{minute:02d}"


def sanitize_earn(raw):
    raw = raw if isinstance(raw, dict) else {}
    tables = [t for t in raw.get("tables", DEFAULT_EARN["tables"]) if isinstance(t, int) and 1 <= t <= 20]
    ops = [o for o in raw.get("ops", DEFAULT_EARN["ops"]) if o in OPS]
    return {
        "enabled": bool(raw.get("enabled", DEFAULT_EARN["enabled"])),
        "seconds_per_correct": _int(raw.get("seconds_per_correct"), DEFAULT_EARN["seconds_per_correct"], 5, 600),
        "daily_cap_minutes": _int(raw.get("daily_cap_minutes"), DEFAULT_EARN["daily_cap_minutes"], 0, 480),
        "min_answer_seconds": _float(raw.get("min_answer_seconds"), DEFAULT_EARN["min_answer_seconds"], 0.0, 30.0),
        "question_timeout_seconds": _int(raw.get("question_timeout_seconds"), DEFAULT_EARN["question_timeout_seconds"], 10, 600),
        "ops": ops or list(DEFAULT_EARN["ops"]),
        "tables": sorted(set(tables)) or list(DEFAULT_EARN["tables"]),
        "max_term": _int(raw.get("max_term"), DEFAULT_EARN["max_term"], 10, 1000),
        "drill_weak": bool(raw.get("drill_weak", DEFAULT_EARN["drill_weak"])),
    }


BLOCKED_PERIOD_LIMIT = 8


def sanitize_blocked_periods(raw, legacy_bedtime=None):
    """The blocked periods of one profile, oldest config shape included.

    A profile written before there were several periods carries a single
    `bedtime` object instead. It becomes the first period rather than being
    dropped, so an existing family keeps their evening after an update.
    """
    default = DEFAULT_PROFILE["blocked_periods"]
    if not isinstance(raw, list):
        if isinstance(legacy_bedtime, dict):
            raw = [{
                "label": "Bedtime",
                "enabled": bool(legacy_bedtime.get("enabled", False)),
                "start": legacy_bedtime.get("start"),
                "end": legacy_bedtime.get("end"),
            }]
        else:
            raw = default

    out = []
    for entry in raw[:BLOCKED_PERIOD_LIMIT]:
        if not isinstance(entry, dict):
            continue
        start = _time_of_day(entry.get("start"), default[0]["start"])
        end = _time_of_day(entry.get("end"), default[0]["end"])
        # A window that starts where it ends blocks nothing, and keeping it
        # would let a typo look like a rule that simply never fires.
        if start == end:
            continue
        label = str(entry.get("label", "")).strip()[:40] or "Blocked"
        out.append({
            "label": label,
            "enabled": bool(entry.get("enabled", False)),
            "start": start,
            "end": end,
        })
    return out


def sanitize_profile(raw):
    raw = raw if isinstance(raw, dict) else {}
    budget_raw = raw.get("budget_minutes")
    budget_raw = budget_raw if isinstance(budget_raw, dict) else {}
    budget = {}
    for day in DAYS:
        budget[day] = _int(budget_raw.get(day), DEFAULT_PROFILE["budget_minutes"][day], 0, 1440)

    blocked = sanitize_blocked_periods(raw.get("blocked_periods"), raw.get("bedtime"))

    warn = [_int(w, 0, 0, 1440) for w in raw.get("warn_minutes", DEFAULT_PROFILE["warn_minutes"])
            if isinstance(w, (int, float))]
    warn = sorted({w for w in warn if w > 0}, reverse=True) or list(DEFAULT_PROFILE["warn_minutes"])

    on_empty = raw.get("on_empty")
    if on_empty not in ("lock", "notify"):
        on_empty = DEFAULT_PROFILE["on_empty"]

    name = str(raw.get("name", DEFAULT_PROFILE["name"]))[:40]

    philosophy = raw.get("philosophy")
    if philosophy not in ("limits", "together"):
        philosophy = DEFAULT_PROFILE["philosophy"]

    return {
        "name": name,
        "philosophy": philosophy,
        # Room for a few sentences now that it is written in a text area,
        # and still a hard stop so a paste cannot grow the config forever.
        "agreement_text": str(raw.get("agreement_text", ""))[:500],
        "agreement_minutes": _int(raw.get("agreement_minutes"), DEFAULT_PROFILE["agreement_minutes"], 0, 1440),
        "break_nudge_minutes": _int(raw.get("break_nudge_minutes"), DEFAULT_PROFILE["break_nudge_minutes"], 0, 480),
        "budget_minutes": budget,
        "blocked_periods": blocked,
        "warn_minutes": warn,
        "on_empty": on_empty,
        "grace_seconds": _int(raw.get("grace_seconds"), DEFAULT_PROFILE["grace_seconds"], 0, 3600),
        "relock_seconds": _int(raw.get("relock_seconds"), DEFAULT_PROFILE["relock_seconds"], 5, 3600),
        "unlock_grace_seconds": _int(raw.get("unlock_grace_seconds"),
                                     DEFAULT_PROFILE["unlock_grace_seconds"], 5, 3600),
        "earn": sanitize_earn(raw.get("earn")),
    }


def sanitize(raw):
    raw = raw if isinstance(raw, dict) else {}
    profiles_raw = raw.get("profiles")
    profiles_raw = profiles_raw if isinstance(profiles_raw, dict) else {}
    profiles = {}
    for key, value in profiles_raw.items():
        if isinstance(key, str) and key.strip() and len(key) <= 40:
            profiles[key] = sanitize_profile(value)
    if not profiles:
        profiles = {"default": sanitize_profile(DEFAULT_PROFILE)}

    active = raw.get("active_profile")
    if active not in profiles:
        active = next(iter(profiles))

    users_raw = raw.get("users")
    users_raw = users_raw if isinstance(users_raw, dict) else {}
    users = {}
    for name, value in users_raw.items():
        if not isinstance(name, str) or not name.strip():
            continue
        value = value if isinstance(value, dict) else {}
        profile = value.get("profile")
        users[name] = {"profile": profile if profile in profiles else active}

    pin = raw.get("pin")
    if not (isinstance(pin, dict) and pin.get("hash") and pin.get("salt")):
        pin = None

    return {
        "version": 1,
        "pin": pin,
        "active_profile": active,
        "profiles": profiles,
        "users": users,
        "demo": bool(raw.get("demo", False)),
    }


def deep_merge(base, patch):
    """Merge patch into base, recursing into dicts, replacing everything else."""
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def hash_pin(pin, salt=None, iterations=PBKDF2_ITERATIONS):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", str(pin).encode(), bytes.fromhex(salt), iterations)
    return {"algo": "pbkdf2_sha256", "salt": salt, "iterations": iterations, "hash": digest.hex()}


def verify_pin(stored, pin):
    if not stored:
        return False
    try:
        candidate = hashlib.pbkdf2_hmac(
            "sha256", str(pin).encode(), bytes.fromhex(stored["salt"]), int(stored["iterations"])
        ).hex()
    except (KeyError, ValueError, TypeError):
        return False
    return secrets.compare_digest(candidate, str(stored.get("hash", "")))


def load(layout):
    text = paths.read_regular(layout.config_path)
    if not text:
        return sanitize(DEFAULT_CONFIG)
    try:
        return sanitize(json.loads(text))
    except (ValueError, TypeError):
        return sanitize(DEFAULT_CONFIG)


def save(layout, config, owner_uid=None):
    paths.private_dir(layout.config_path.parent, owner_uid=owner_uid, scrub=False)
    paths.write_private(layout.config_path, json.dumps(sanitize(config), indent=2) + "\n", owner_uid=owner_uid)


def profile_for(config, username):
    key = config.get("users", {}).get(username, {}).get("profile") or config["active_profile"]
    if key not in config["profiles"]:
        key = config["active_profile"]
    return key, config["profiles"][key]
