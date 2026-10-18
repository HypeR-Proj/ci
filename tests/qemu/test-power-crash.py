#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Runtime stderr must not consume independently delivered console evidence."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

from session import Session


spec = importlib.util.spec_from_file_location(
    'power_crash', Path(__file__).with_name('verify-power-crash.py'))
power_crash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(power_crash)


class PowerCrashTests(unittest.TestCase):
    def test_marker_and_disconnect_may_arrive_in_either_order(self):
        marker = b'HYPER_POWER_CRASH state=powered-off\r\n'
        disconnect = b'[vmm] virtual machine disconnected\r\n'
        prompt = b'hyper-sh$ '
        for output in (marker + disconnect + prompt,
                       disconnect + marker + prompt,
                       disconnect + prompt + marker):
            with self.subTest(output=output), tempfile.TemporaryDirectory() as directory:
                script = (
                    'import sys,time; '
                    f'sys.stdout.buffer.write({marker!r}); sys.stdout.flush(); '
                    'sys.stdin.buffer.readline(); '
                    f'sys.stdout.buffer.write({output!r}); sys.stdout.flush(); '
                    'time.sleep(30)')
                with Session([sys.executable, '-u', '-c', script],
                             Path(directory) / 'serial.log') as session:
                    session.await_text(marker.replace(b'\r', b''), timeout=2)
                    boundary = session.mark()
                    session.send(b'crash\n')
                    power_crash.await_power_loss(session, 'powered-off', boundary)
                self.assertIsNotNone(session.process.returncode)


if __name__ == '__main__':
    unittest.main()
