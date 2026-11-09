#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Review-Regressionen: ausschließlich Tempwurzeln und Fake-Unix-Socket."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from test_ncpin import FakeNextcloudSocket, load_ncpin


class RegisteredPathFramingTests(unittest.TestCase):
    def setUp(self):
        self.ncpin = load_ncpin()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "registered")
        os.mkdir(self.root)
        self.inside = os.path.join(self.root, "inside.txt")
        self.outside = os.path.join(self.tmp.name, "outside.txt")
        for path in (self.inside, self.outside):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("fixture")
        self.config = os.path.join(self.tmp.name, "fake.cfg")
        with open(self.config, "w", encoding="utf-8") as handle:
            handle.write("[Accounts]\n")
        self.socket_path = os.path.join(self.tmp.name, "fake.sock")
        env = mock.patch.dict(os.environ, {
            "NCPIN_SOCKET": self.socket_path, "NCPIN_CONFIG": self.config,
        })
        env.start()
        self.addCleanup(env.stop)

    def run_online(self, chunks, target):
        server = FakeNextcloudSocket(
            self.socket_path, [self.root], handshake_chunks=chunks).start()
        self.addCleanup(server.close)
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(self.ncpin, "rename_transport_context") as fallback, \
                mock.patch.object(self.ncpin, "socket_api_removed_hint", return_value=None), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = self.ncpin.main(["online", "--json", target])
        fallback.assert_not_called()
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "fixture")
        return rc, stdout.getvalue(), list(server.commands)

    def test_unterminated_root_prefix_cannot_authorize_parent(self):
        # Der echte Root wäre .../registered; der Burst endet schon am Elternpfad.
        partial = ("REGISTER_PATH:" + self.tmp.name).encode("utf-8")
        rc, _out, commands = self.run_online([partial], self.outside)
        self.assertFalse(any(cmd.startswith("MAKE_") for cmd in commands), commands)
        self.assertEqual(rc, 1)

    def test_complete_root_does_not_validate_trailing_partial_root(self):
        response = ("REGISTER_PATH:%s\nREGISTER_PATH:%s" %
                    (self.root, self.tmp.name)).encode("utf-8")
        rc, out, commands = self.run_online([response], self.outside)
        self.assertFalse(any(cmd.startswith("MAKE_") for cmd in commands), commands)
        self.assertEqual(rc, 3)
        self.assertIn("error", json.loads(out)[0])

    def test_fragmented_complete_root_still_authorizes_inside(self):
        record = ("REGISTER_PATH:%s\n" % self.root).encode("utf-8")
        rc, out, commands = self.run_online([record[:-1], record[-1:]], self.inside)
        self.assertEqual(rc, 0)
        self.assertIn("MAKE_ONLINE_ONLY:" + os.path.realpath(self.inside), commands)
        self.assertEqual(json.loads(out)[0]["target"], "online")


if __name__ == "__main__":
    unittest.main()
