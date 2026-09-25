#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise serial transport using small controlled child processes, not QEMU."""
from pathlib import Path
import sys
import tempfile
import time
import unittest

from session import Session
from guest_console import append_console_output


class SessionTests(unittest.TestCase):
    def session(self, script, log, **options):
        return Session([sys.executable, '-u', '-c', script], log, **options)

    def test_raw_log_and_consumed_normalized_buffer(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'raw.log'
            with self.session(
                "import sys,time; sys.stdout.buffer.write(b'first\\r\\nsecond\\r\\n'); "
                "sys.stdout.flush(); value=sys.stdin.buffer.readline(); "
                "sys.stdout.buffer.write(value); sys.stdout.flush(); time.sleep(30)", log
            ) as session:
                self.assertEqual(session.await_text(b'first\\n'), b'first\n')
                self.assertEqual(session.await_text(b'second\\n', match_only=True), b'second\n')
                session.send(b'command\n')
                self.assertEqual(session.await_text(b'command\\n'), b'command\n')
            self.assertEqual(log.read_bytes(), b'first\r\nsecond\r\ncommand\n')
            self.assertIsNotNone(session.process.returncode)

    def test_filtered_console_rejoins_ci_wakeup_and_preserves_raw_log(self):
        raw = (b'\nGUEST_WAKE_06<4>[   84.456723] HypeR: '
               b'masked virtual timer PPI without an active vCPU\n\n~ # ')
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'raw.log'
            script = (f'import sys,time; data={raw!r}; '
                      '[ (sys.stdout.buffer.write(bytes([b])), sys.stdout.flush(), '
                      'time.sleep(0.001)) for b in data ]; time.sleep(30)')
            with self.session(script, log, output_filter=append_console_output) as session:
                self.assertEqual(session.await_text(rb'\nGUEST_WAKE_06\n~ # '),
                                 b'\nGUEST_WAKE_06\n~ # ')
            self.assertEqual(log.read_bytes(), raw)

    def test_filter_cannot_hide_custom_failure_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'kernel failure'):
                with self.session(
                    "import time; print('<4>[ 1.0] HypeR: CUSTOM_FAILURE'); time.sleep(30)",
                    Path(directory) / 'log', failures=(b'CUSTOM_FAILURE',),
                    output_filter=append_console_output
                ) as session:
                    session.await_text(b'READY')

    def test_timeout_reaps_owned_process(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TimeoutError):
                with self.session('import time; time.sleep(30)', Path(directory) / 'log') as session:
                    session.await_text(b'never', timeout=0.05)
            self.assertIsNotNone(session.process.returncode)
            self.assertTrue(session.process.stdin.closed)
            self.assertTrue(session.process.stdout.closed)

    def test_early_exit_retains_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'log'
            with self.assertRaisesRegex(RuntimeError, 'QEMU exited with 7:.*broken'):
                with self.session("import sys; print('broken'); sys.exit(7)", log) as session:
                    session.process.wait(timeout=3)
                    session.await_text(b'never')
            self.assertEqual(log.read_bytes(), b'broken\n')

    def test_failure_marker_wins_over_success_in_same_read(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'kernel failure'):
                with self.session("import time; print('kernel panic READY'); time.sleep(30)",
                                  Path(directory) / 'log') as session:
                    session.await_text(b'READY')

    def test_exception_cleanup_escalates_to_kill(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'scenario failure'):
                with self.session(
                    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    "print('READY'); time.sleep(30)", Path(directory) / 'log', cleanup_timeout=0.05
                ) as session:
                    session.await_text(b'READY')
                    raise ValueError('scenario failure')
            self.assertLess(session.process.returncode, 0)

    def test_closed_serial_does_not_spin_until_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, 'closed serial output'):
                with self.session('import os,time; os.close(1); os.close(2); time.sleep(30)',
                                  Path(directory) / 'log') as session:
                    session.await_text(b'never', timeout=10)
            self.assertLess(time.monotonic() - started, 5)


if __name__ == '__main__':
    unittest.main()
