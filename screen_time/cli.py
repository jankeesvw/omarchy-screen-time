"""The command line client. Prints JSON, because the widget reads it too.

The PIN is never an argument. /proc/<pid>/cmdline is world readable, so a
parent typing a PIN on the command line would be handing it to anyone with a
shell on the machine; it is read from stdin or asked for interactively.
"""

import argparse
import getpass
import json
import os
import sys

from . import paths, proto


def _fail(message, code=1):
    print(json.dumps({"ok": False, "error": message}))
    return code


def _ask_pin(args):
    if args.pin_stdin:
        return sys.stdin.readline().rstrip("\n")
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    return getpass.getpass("PIN: ")


def _request(payload, timeout=5):
    return proto.request(paths.client_socket_candidates(), payload, timeout=timeout)


def _emit(payload, human=False):
    if human:
        print(_human(payload))
    else:
        print(json.dumps(payload))
    return 0 if payload.get("ok") else 1


def _clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _human(payload):
    if not payload.get("ok"):
        return f"error: {payload.get('error')}"
    if "remaining_seconds" not in payload:
        return json.dumps(payload, indent=2)
    lines = [
        f"{payload['profile_name']} ({payload['user']}) on {payload['day']}",
        f"  left        {_clock(payload['remaining_seconds'])}   [{payload['phase']}]",
        f"  budget      {_clock(payload['budget_seconds'])}"
        f"  earned {_clock(payload['earned_seconds'])}"
        f"  granted {_clock(payload['granted_seconds'])}",
        f"  spent       {_clock(payload['spent_seconds'])}",
    ]
    if payload.get("lock_in_seconds") is not None:
        lines.append(f"  lock in     {payload['lock_in_seconds']}s")
    earn = payload.get("earn", {})
    if earn.get("enabled"):
        lines.append(f"  earning     {_clock(earn['room_seconds'])} of "
                     f"{_clock(earn['cap_seconds'])} left, "
                     f"{earn['seconds_per_correct']}s per correct answer")
    return "\n".join(lines)


def cmd_status(args):
    return _emit(_request({"cmd": "status"}), args.human)


def cmd_ping(args):
    return _emit(_request({"cmd": "ping"}), args.human)


def cmd_history(args):
    return _emit(_request({"cmd": "history", "days": args.days}), args.human)


def cmd_watch(args):
    sock = proto.connect(paths.client_socket_candidates(), timeout=None)
    try:
        proto.write_line(sock, {"cmd": "watch"})
        reader = proto.LineReader(sock)
        while True:
            payload = reader.read()
            if payload is None:
                return 0
            print(json.dumps(payload), flush=True)
    finally:
        sock.close()


def cmd_quiz(args):
    """Practice in the terminal. The same calls the panel will make."""
    while True:
        response = _request({"cmd": "quiz.next"})
        if not response.get("ok"):
            return _emit(response, args.human)
        question = response["question"]
        if not args.human:
            return _emit(response, False)
        try:
            given = input(f"{question['text']} = ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        verdict = _request({"cmd": "quiz.answer", "id": question["id"], "answer": given})
        if verdict.get("error") == "too_fast":
            print(f"  too fast, wait another {verdict['wait_seconds']}s")
            continue
        if not verdict.get("ok"):
            print(f"  {verdict.get('error')}")
            continue
        if verdict["correct"]:
            print(f"  correct, +{verdict['reward_seconds']}s   (left: {_clock(verdict['remaining_seconds'])})")
        else:
            print(f"  wrong, it was {verdict['answer']}")
        if not args.keep_going:
            return 0


def cmd_answer(args):
    return _emit(_request({"cmd": "quiz.answer", "id": args.id, "answer": args.answer}), args.human)


def cmd_grant(args):
    return _emit(_request({"cmd": "grant", "minutes": args.minutes, "pin": _ask_pin(args)}), args.human)


def cmd_pause(args):
    return _emit(_request({"cmd": "pause", "value": args.value, "pin": _ask_pin(args)}), args.human)


def cmd_lock(args):
    return _emit(_request({"cmd": "lock", "pin": _ask_pin(args)}), args.human)


def cmd_idle(args):
    return _emit(_request({"cmd": "idle", "value": args.value}), args.human)


def cmd_demo(args):
    return _emit(_request({"cmd": "demo", "value": args.value, "pin": _ask_pin(args)}), args.human)


def cmd_config_get(args):
    return _emit(_request({"cmd": "config.get", "pin": _ask_pin(args)}), args.human)


def cmd_config_set(args):
    text = paths.read_regular(args.file) if args.file != "-" else sys.stdin.read()
    if not text:
        return _fail("could not read the config file")
    try:
        incoming = json.loads(text)
    except ValueError:
        return _fail("that file is not valid json")
    return _emit(_request({"cmd": "config.set", "config": incoming, "pin": _ask_pin(args)}), args.human)


def cmd_reflect(args):
    return _emit(_request({"cmd": "reflect", "text": " ".join(args.words)}), args.human)


def cmd_config_patch(args):
    try:
        patch = json.loads(args.patch)
    except ValueError:
        return _fail("that is not valid json")
    if not isinstance(patch, dict):
        return _fail("the patch must be a json object")
    return _emit(_request({"cmd": "config.patch", "patch": patch, "pin": _ask_pin(args)}), args.human)


def cmd_pin_set(args):
    new_pin = getpass.getpass("new PIN: ") if sys.stdin.isatty() else sys.stdin.readline().strip()
    old = ""
    if sys.stdin.isatty():
        again = getpass.getpass("once more: ")
        if again != new_pin:
            return _fail("the two entries do not match")
        old = getpass.getpass("current PIN (empty if there is none yet): ")
    return _emit(_request({"cmd": "pin.set", "new_pin": new_pin, "pin": old}), args.human)


def build_parser():
    parser = argparse.ArgumentParser(prog="omarchy-screen-time", description="Screen Time for kids")
    parser.add_argument("--human", action="store_true", help="readable output instead of json")
    parser.add_argument("--pin-stdin", action="store_true", help="read the PIN from stdin")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping").set_defaults(func=cmd_ping)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("watch").set_defaults(func=cmd_watch)

    history = sub.add_parser("history")
    history.add_argument("--days", type=int, default=14)
    history.set_defaults(func=cmd_history)

    quiz = sub.add_parser("quiz", help="ask for a question, or practice in the terminal")
    quiz.add_argument("--keep-going", action="store_true")
    quiz.set_defaults(func=cmd_quiz)

    answer = sub.add_parser("answer")
    answer.add_argument("id")
    answer.add_argument("answer")
    answer.set_defaults(func=cmd_answer)

    grant = sub.add_parser("grant", help="give or take minutes (parent)")
    grant.add_argument("minutes", type=int)
    grant.set_defaults(func=cmd_grant)

    pause = sub.add_parser("pause")
    pause.set_defaults(func=cmd_pause, value=True)
    resume = sub.add_parser("resume")
    resume.set_defaults(func=cmd_pause, value=False)

    sub.add_parser("lock").set_defaults(func=cmd_lock)

    reflect = sub.add_parser("reflect", help="write a note about your own screen time")
    reflect.add_argument("words", nargs="+")
    reflect.set_defaults(func=cmd_reflect)

    idle = sub.add_parser("idle", help="for the hypridle hook")
    idle.add_argument("value", choices=["on", "off"])
    idle.set_defaults(func=lambda a: cmd_idle_wrap(a))

    demo = sub.add_parser("demo")
    demo.add_argument("value", choices=["on", "off"])
    demo.set_defaults(func=lambda a: cmd_demo_wrap(a))

    config = sub.add_parser("config")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("get").set_defaults(func=cmd_config_get)
    config_set = config_sub.add_parser("set")
    config_set.add_argument("file", help="a json file, or - for stdin")
    config_set.set_defaults(func=cmd_config_set)
    config_patch = config_sub.add_parser("patch", help="merge a partial change into your profile")
    config_patch.add_argument("patch", help="a json object, e.g. '{\"earn\": {\"enabled\": false}}'")
    config_patch.set_defaults(func=cmd_config_patch)

    pin = sub.add_parser("pin")
    pin_sub = pin.add_subparsers(dest="pin_command", required=True)
    pin_sub.add_parser("set").set_defaults(func=cmd_pin_set)

    return parser


def cmd_idle_wrap(args):
    args.value = args.value == "on"
    return cmd_idle(args)


def cmd_demo_wrap(args):
    args.value = args.value == "on"
    return cmd_demo(args)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConnectionError as exc:
        return _fail(f"no daemon: {exc}")
    except (BrokenPipeError, KeyboardInterrupt):
        return 130
