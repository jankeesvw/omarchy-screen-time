"""Talking to the graphical session: is it in use, notify it, lock it.

`loginctl` is the supported interface for all three, and it is the same call in
both install modes. Only the lock command differs: as root we can lock any
session, as the user we ask the desktop to lock itself.

Note that properties are read as KEY=value lines rather than with --value:
systemd prints --value output in its own order, not in the order you asked, so
positional parsing quietly pairs the wrong value with the wrong name.
"""

import os
import pwd
import shutil
import subprocess

LOCK_COMMANDS = ["omarchy-system-lock", "omarchy-lock-screen", "hyprlock"]


def _run(argv, timeout=5, **kwargs):
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False, **kwargs
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _properties(argv):
    result = _run(argv)
    if not result or result.returncode != 0:
        return {}
    out = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def username_for(uid):
    try:
        return pwd.getpwuid(int(uid)).pw_name
    except (KeyError, ValueError):
        return str(uid)


def graphical_sessions(uid):
    """Session ids of this uid's graphical sessions, most likely first."""
    props = _properties(["loginctl", "show-user", str(int(uid)), "-p", "Sessions"])
    ids = props.get("Sessions", "").split()
    out = []
    for session_id in ids:
        info = _properties(["loginctl", "show-session", session_id,
                            "-p", "Type", "-p", "Class", "-p", "Active",
                            "-p", "LockedHint", "-p", "State"])
        if not info:
            continue
        if info.get("Class") != "user":
            continue
        if info.get("Type") not in ("wayland", "x11"):
            continue
        out.append((session_id, info))
    out.sort(key=lambda item: item[1].get("Active") != "yes")
    return out


class SessionWatcher:
    """Whether this account is actually looking at the screen right now."""

    def __init__(self, uid):
        self.uid = int(uid)
        self.session_id = None
        self.active = False
        self.locked = False
        self.present = False

    def poll(self):
        sessions = graphical_sessions(self.uid)
        if not sessions:
            self.session_id, self.present, self.active, self.locked = None, False, False, False
            return self
        session_id, info = sessions[0]
        self.session_id = session_id
        self.present = True
        self.active = info.get("Active") == "yes" and info.get("State") in ("active", "online")
        self.locked = info.get("LockedHint") == "yes"
        if not self.locked:
            shell = shell_locked(self.uid)
            if shell is not None:
                self.locked = shell
        return self

    @property
    def in_use(self):
        return self.present and self.active and not self.locked


def _user_env(uid):
    uid = int(uid)
    runtime = f"/run/user/{uid}"
    if os.geteuid() != 0 and uid == os.getuid():
        # User mode: the daemon runs inside the session, so it already holds
        # WAYLAND_DISPLAY, the Hyprland signature and everything else the lock
        # and notify commands need. Keep all of it.
        env = dict(os.environ)
        env.setdefault("XDG_RUNTIME_DIR", runtime)
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        return env
    return {
        "XDG_RUNTIME_DIR": runtime,
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": pwd.getpwuid(uid).pw_dir if uid else "/root",
    }


def _as_user(uid, argv, timeout=5):
    """Run a command in the account's own session, dropping privileges if needed."""
    env = _user_env(uid)
    kwargs = {"env": env}
    if os.geteuid() == 0 and int(uid) != 0:
        entry = pwd.getpwuid(int(uid))
        kwargs["user"] = entry.pw_uid
        kwargs["group"] = entry.pw_gid
    return _run(argv, timeout=timeout, **kwargs)


def shell_locked(uid):
    """Whether the Omarchy shell's own lock screen is up.

    Quattro's lock screen does not set logind's LockedHint, so LockedHint says
    "no" while the screen is very much locked. The shell itself does know.
    Returns None when there is no shell to ask, so LockedHint stays the answer
    on anything that is not Omarchy.
    """
    if not shutil.which("omarchy-shell"):
        return None
    result = _as_user(uid, ["omarchy-shell", "lock", "isLocked"])
    if result is None or result.returncode != 0:
        return None
    out = result.stdout.strip().lower()
    if out in ("true", "false"):
        return out == "true"
    return None


def notify(uid, title, body, urgency="normal", tag=None):
    argv = ["notify-send", "-a", "Screen Time", "-u", urgency]
    if tag:
        argv += ["-h", f"string:x-canonical-private-synchronous:screen-time-{tag}"]
    argv += [str(title), str(body)]
    return _as_user(uid, argv) is not None


def lock(uid, session_id=None):
    """Lock the account's screen. Returns the command that did it, or None.

    SCREEN_TIME_LOCK_COMMAND replaces the whole search, which is how the test
    suite proves the lock path without locking the tester out of their own
    screen. It is a program name or path, never a shell string, so nothing in
    it can be split into extra arguments.
    """
    override = os.environ.get("SCREEN_TIME_LOCK_COMMAND")
    if override:
        result = _as_user(uid, [override], timeout=10)
        if result is not None and result.returncode == 0:
            return override
        return None
    if os.geteuid() == 0 and session_id:
        result = _run(["loginctl", "lock-session", str(session_id)])
        if result and result.returncode == 0:
            return "loginctl lock-session"
    for command in LOCK_COMMANDS:
        if shutil.which(command):
            result = _as_user(uid, [command], timeout=10)
            if result is not None and result.returncode == 0:
                return command
    if session_id:
        result = _run(["loginctl", "lock-session", str(session_id)])
        if result and result.returncode == 0:
            return "loginctl lock-session"
    return None
