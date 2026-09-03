# Screen Time

Screen Time for kids on Omarchy. A minute budget that visibly ticks down, locks the screen at zero, and lets extra minutes be earned with multiplication tables and math problems.

## What is here now

`bin/omarchy-screen-timed` is the daemon. It counts the time, warns, and locks the screen. It is the only thing that touches the state and the config.

`bin/omarchy-screen-time` is the client. It talks to the daemon over a unix socket and prints JSON, so the QML widget reads the same thing you see from the command line.

The plugin itself ships three components from one manifest, and they share a single connection to the daemon:

- **Service.qml** (`keepLoaded`) holds the one `omarchy-screen-time watch` stream and is where the state lives. It keeps counting down locally between daemon ticks, so the last minutes read as a clock.
- **BarWidget.qml** is the pill in the bar plus the panel behind it. The pill shows a glyph for the phase (hourglass, clock when idle, pause, lock, moon for bedtime) and the time that is left; it warns in amber below the last warning threshold and turns red when the time is up. Click it and the panel opens: the child answers math problems to earn minutes right there, and a parent unlocks fixed choices (+15, +30, +60, -15, pause) with the PIN. The PIN goes to the daemon over stdin, never as an argument, and is forgotten when the panel closes.
- **Countdown.qml** is a small card at the bottom of the screen that appears once the time drops below five minutes, and during the grace period counts down to the lock. It takes no input and never blocks a click.
- **SettingsWindow.qml** is the parent's settings in a floating window of its own, opened from the panel after the PIN unlock: minutes per weekday, bedtime, and the earning knobs (on or off, division problems, which tables, the reward, the daily cap). Every change is a partial `config patch` to the daemon and applies immediately. Changing the PIN itself stays on the command line: `omarchy-screen-time pin set`, which asks for the current PIN first.

```
omarchy-screen-time --human status
omarchy-screen-time --human quiz --keep-going    # practice math problems in the terminal
omarchy-screen-time grant 15                     # give minutes as a parent, PIN via stdin
omarchy-screen-time history --days 7
omarchy-screen-time watch                        # NDJSON stream for the widget
```

## Two installs

**Soft.** `bin/omarchy-screen-time-service enable` sets the daemon up as a user service in the child's session. Everything lives under `~/.local/state/omarchy-screen-time` and `~/.config/omarchy-screen-time`. A child who knows `systemctl --user stop` can turn this off. For young children that is fine: it works like a kitchen timer you can see running.

**Strict.** As soon as `/etc/omarchy-screen-time/` exists, the same daemon runs as root, with the state in `/var/lib/omarchy-screen-time/` and the socket in `/run/omarchy-screen-time/`. The child cannot write the files and cannot stop the unit. The install script for this is still to come.

The daemon itself does not know which mode it runs in: `screen_time/paths.py` decides that once, and no path is built anywhere else in the code.

## How time is counted

Only while the session exists, is active, and is not locked. That is read from `loginctl`, and it works the same in both modes. On Omarchy the shell's lock screen does not set logind's `LockedHint`, so the daemon also asks the shell itself (`omarchy-shell lock isLocked`); on anything else `LockedHint` remains the answer.

If you want it more precise, let hypridle join in. Two lines in `hypr/hypridle.conf` turn "the screen is on" into "somebody is actually sitting there":

```
listener {
    timeout = 120
    on-timeout = omarchy-screen-time idle on
    on-resume = omarchy-screen-time idle off
}
```

Setting the clock back does not help. The counter runs on `CLOCK_BOOTTIME` and the wall clock may only nudge it by 300 seconds per tick; larger jumps are ignored and noted in that day's ledger. The last known time is kept on disk, so setting the clock back while the daemon is off does not work either.

## Earning minutes

The question comes from the daemon and the answer is checked there. The client never sees the right answer, because the client runs on the child's machine.

Also: an answer within `min_answer_seconds` does not count, the same question cannot pay out twice, there is a daily cap on the bonus, and tables that go wrong more often come around more often.

## The agreement mode

Set `"philosophy": "together"` on a profile (shown as "Agreement" in the settings window) and the plugin changes character, along the lines of Alfie Kohn's argument against rewards and control: working with the child instead of doing things to them.

Nothing locks and nothing is earned. The widget becomes a mirror that shows time spent, in neutral colours, never counting down. Instead of a budget there is an agreement the family writes together, in the child's own words, shown in the panel; when the day passes the agreed time there is one calm notification stating the fact, and that is all. A break nudge (`break_nudge_minutes`) can point out an unbroken stretch, framed as self care rather than discipline.

The panel asks "How is it going?" and keeps the child's notes (`omarchy-screen-time reflect ...` from the terminal does the same). Those notes live in the child's own state directory and go to nobody: showing them is the child's choice. There are no parent buttons in this mode; revisiting the agreement opens the settings, meant to be done side by side.

The two philosophies live per profile, so one child can have limits while another has an agreement, and a family can start strict and grow towards together.

## Config

One file, with a profile per child. The daemon clamps every value on read: a budget of -5 becomes 0, a table of 999 disappears, an unknown `on_empty` becomes `lock`. A daemon that crashes on a bad config is unlimited screen time.

```json
{
  "active_profile": "sam",
  "profiles": {
    "sam": {
      "name": "Sam",
      "budget_minutes": {"mon": 60, "tue": 60, "wed": 90, "thu": 60, "fri": 90, "sat": 120, "sun": 120},
      "bedtime": {"enabled": true, "start": "20:00", "end": "07:00"},
      "warn_minutes": [15, 5, 1],
      "on_empty": "lock",
      "grace_seconds": 60,
      "relock_seconds": 30,
      "unlock_grace_seconds": 120,
      "earn": {
        "enabled": true,
        "seconds_per_correct": 30,
        "daily_cap_minutes": 30,
        "ops": ["mul", "div"],
        "tables": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
      }
    }
  }
}
```

`grace_seconds` is the time between "your time is up" and the lock. `relock_seconds` is only a retry when the lock did not take. `unlock_grace_seconds` starts when somebody unlocks the screen while the budget is zero: only somebody holding the account password can do that, and on a child's machine that is you, so you get a calm window to hand out minutes instead of racing a countdown. In the soft install, where the child knows their own password, you set it low instead.

The parent PIN is stored hashed (pbkdf2, 600k rounds) and never travels as a command line argument, because `/proc/<pid>/cmdline` is readable by everyone on the machine.

## Testing

```
python3 tests/test_core.py
```

Runs without a daemon and without a desktop. Covers the clock that refuses to go back, the writes that do not follow a symlink, the config that clamps everything, and the math problems.

For a test with a real daemon: point `SCREEN_TIME_ROOT` at an empty directory, set `SCREEN_TIME_TICK_SECONDS=1` and point `SCREEN_TIME_LOCK_COMMAND` at a script that only writes a line. Then you can walk the whole flow up to and including the lock without locking yourself out.

## What comes next

The install script for the strict mode, and maybe an "unlimited apps" list and buttons on the lock screen itself.
