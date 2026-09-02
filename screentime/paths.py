"""Where config, state and the control socket live.

Three layouts, resolved in this order:

  test    SCREENTIME_ROOT is set, everything below it. Used by the test runner.
  system  /etc/omarchy-screentime exists, so the strict install is in place:
          root owns the state and the child account cannot write it.
  user    the soft install: everything under the user's own XDG directories.

Nothing outside this module builds a path of its own, which is what lets the
same daemon run in both modes.
"""

import os
import shutil
import stat
import tempfile
from pathlib import Path

APP = "omarchy-screentime"

SYSTEM_CONFIG_DIR = Path("/etc") / APP
SYSTEM_STATE_DIR = Path("/var/lib") / APP
SYSTEM_RUNTIME_DIR = Path("/run") / APP


class Insecure(Exception):
    """A directory or file is not owned by us, or is not what it claims to be."""


class Layout:
    def __init__(self, mode, config_path, state_dir, socket_path):
        self.mode = mode
        self.config_path = Path(config_path)
        self.state_dir = Path(state_dir)
        self.socket_path = Path(socket_path)

    @property
    def runtime_dir(self):
        return self.socket_path.parent

    def user_state_dir(self, uid):
        """State for one account. In system mode several accounts share a root."""
        if self.mode == "system":
            return self.state_dir / "users" / str(int(uid))
        return self.state_dir

    def __repr__(self):
        return f"<Layout {self.mode} config={self.config_path} state={self.state_dir}>"


def _xdg(name, fallback):
    value = os.environ.get(name)
    return Path(value) if value else Path(fallback)


def detect(root=None):
    """Pick the layout for this process."""
    root = root or os.environ.get("SCREENTIME_ROOT")
    if root:
        base = Path(root)
        return Layout("test", base / "config.json", base / "state", base / "sock")

    if SYSTEM_CONFIG_DIR.exists():
        return Layout(
            "system",
            SYSTEM_CONFIG_DIR / "config.json",
            SYSTEM_STATE_DIR,
            SYSTEM_RUNTIME_DIR / "sock",
        )

    home = Path.home()
    return Layout(
        "user",
        _xdg("XDG_CONFIG_HOME", home / ".config") / APP / "config.json",
        _xdg("XDG_STATE_HOME", home / ".local/state") / APP,
        _xdg("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}") / APP / "sock",
    )


def client_socket_candidates():
    """Sockets a client should try, most specific first."""
    root = os.environ.get("SCREENTIME_ROOT")
    if root:
        return [Path(root) / "sock"]
    home = Path.home()
    return [
        SYSTEM_RUNTIME_DIR / "sock",
        _xdg("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}") / APP / "sock",
    ]


def private_dir(path, mode=0o700, owner_uid=None, scrub=True):
    """Create or repair a directory that only its owner may read.

    The repair is unconditional and covers the contents, because a directory
    that is 0700 today can still hold 0644 files, or entries somebody else
    planted while it was wider. Anything that is not a regular file or a
    directory is removed: our own code never puts one there.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(mode=mode, parents=True, exist_ok=True)

    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise Insecure(f"{path} is a symlink")
    if not stat.S_ISDIR(st.st_mode):
        raise Insecure(f"{path} is not a directory")
    want_uid = os.geteuid() if owner_uid is None else int(owner_uid)
    if st.st_uid != want_uid:
        raise Insecure(f"{path} is owned by uid {st.st_uid}, not {want_uid}")

    os.chmod(path, mode)
    if owner_uid is not None and os.geteuid() == 0:
        os.chown(path, int(owner_uid), -1)

    if scrub:
        _scrub(path, owner_uid)
    return path


def _scrub(path, owner_uid):
    for entry in os.scandir(path):
        est = os.lstat(entry.path)
        if stat.S_ISDIR(est.st_mode):
            os.chmod(entry.path, 0o700)
            _scrub(Path(entry.path), owner_uid)
        elif stat.S_ISREG(est.st_mode):
            os.chmod(entry.path, 0o600)
            if owner_uid is not None and os.geteuid() == 0:
                os.chown(entry.path, int(owner_uid), -1)
        else:
            try:
                os.unlink(entry.path)
            except OSError:
                shutil.rmtree(entry.path, ignore_errors=True)


def write_private(path, text, owner_uid=None):
    """Write through a temporary file and rename.

    A plain `>` follows a symlink sitting on the destination; rename(2) replaces
    the path itself, so a planted link cannot redirect the write.
    """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        if owner_uid is not None and os.geteuid() == 0:
            os.fchown(fd, int(owner_uid), -1)
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_regular(path):
    """Read a file, refusing anything that is not a plain regular file."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        with os.fdopen(fd, "r") as handle:
            return handle.read()
    except OSError:
        return None
