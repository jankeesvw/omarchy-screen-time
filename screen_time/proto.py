"""One JSON object per line over a unix socket.

The client never reads the state or the config itself. Everything goes through
the daemon, which is what makes the strict install possible: in that mode the
child's account cannot write the files at all, and the socket is the only door.
"""

import json
import os
import socket
import struct

MAX_LINE = 64 * 1024


class ProtocolError(Exception):
    pass


def peer_uid(sock):
    data = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", data)
    return uid


def write_line(sock, payload):
    sock.sendall((json.dumps(payload) + "\n").encode())


class LineReader:
    def __init__(self, sock):
        self.sock = sock
        self.buffer = b""

    def read(self):
        while b"\n" not in self.buffer:
            if len(self.buffer) > MAX_LINE:
                raise ProtocolError("line too long")
            chunk = self.sock.recv(4096)
            if not chunk:
                return None
            self.buffer += chunk
        line, _, rest = self.buffer.partition(b"\n")
        self.buffer = rest
        try:
            return json.loads(line.decode())
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProtocolError("not json") from exc


def _connect(sock, path):
    """Connect, working around the 108 byte limit on unix socket paths."""
    try:
        sock.connect(str(path))
        return
    except OSError:
        pass
    directory = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
    previous = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fchdir(directory)
        sock.connect(path.name)
    finally:
        os.fchdir(previous)
        os.close(directory)
        os.close(previous)


def connect(candidates, timeout=5):
    last = None
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            _connect(sock, path)
            return sock
        except OSError as exc:
            last = exc
    raise ConnectionError(str(last) if last else "no screen_time daemon is listening")


def request(candidates, payload, timeout=5):
    sock = connect(candidates, timeout)
    try:
        write_line(sock, payload)
        reader = LineReader(sock)
        response = reader.read()
        if response is None:
            raise ProtocolError("daemon closed the connection")
        return response
    finally:
        sock.close()
