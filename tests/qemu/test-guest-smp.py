#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for multiplexed host logs and guest console output."""

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'guest_smp', Path(__file__).with_name('verify-guest-smp.py'))
guest_smp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guest_smp)


class ConsoleOutputTests(unittest.TestCase):
    warning = (b'<4>[   66.643508] HypeR: masked virtual timer PPI '
               b'without an active vCPU\r\n')

    def normalize(self, chunks):
        pending = bytearray()
        for chunk in chunks:
            guest_smp.append_console_output(pending, chunk)
        return pending

    def test_ci_failure_prompt_survives_every_read_boundary(self):
        data = b'~' + self.warning + b' # \x1b[6n'
        for split in range(len(data) + 1):
            with self.subTest(split=split):
                self.assertEqual(self.normalize((data[:split], data[split:])), b'~ # \x1b[6n')
        self.assertEqual(self.normalize(bytes([byte]) for byte in data), b'~ # \x1b[6n')

    def test_host_prompt_and_guest_result_rejoin_across_multiple_logs(self):
        data = b'hyper-' + self.warning + b'sh$ \r\nSMP_ON' + self.warning + b'LINE=0-3\r\n'
        self.assertEqual(self.normalize((data,)), b'hyper-sh$ \nSMP_ONLINE=0-3\n')

    def test_guest_boot_diagnostics_are_preserved(self):
        data = b'[   63.704984] CPU1: Booted secondary processor\r\n~ # '
        self.assertEqual(self.normalize((data,)), data.replace(b'\r', b''))

    def test_native_acceptance_retains_guest_crlf_across_host_records(self):
        expected = b'HYPER_GUEST_CONSOLE_RX\r\n'
        data = b'HYPER_GUEST' + self.warning + b'_CONSOLE' + self.warning + b'_RX\r\n'
        for split in range(len(data) + 1):
            with self.subTest(split=split):
                pending = bytearray()
                for chunk in (data[:split], data[split:]):
                    guest_smp.append_console_output(pending, chunk, preserve_cr=True)
                self.assertEqual(pending, expected)

    def test_preserving_cr_does_not_invent_tty_conversion(self):
        pending = bytearray()
        guest_smp.append_console_output(pending, b'result\n', preserve_cr=True)
        self.assertEqual(pending, b'result\n')

    def test_preserving_cr_still_rejects_fatal_host_records(self):
        with self.assertRaisesRegex(RuntimeError, 'kernel failure'):
            guest_smp.append_console_output(
                bytearray(), b'<0>[ 1.000000] HypeR KERNEL PANIC\r\n', preserve_cr=True)

    def test_host_panic_is_never_filtered_out(self):
        data = b'<0>[ 66.700000] HypeR KERNEL PANIC - NOT SYNCING\r\n'
        for split in range(len(data) + 1):
            with self.subTest(split=split), self.assertRaisesRegex(RuntimeError, 'kernel failure'):
                self.normalize((data[:split], data[split:]))

    def test_guest_panic_split_by_host_log_is_detected(self):
        with self.assertRaisesRegex(RuntimeError, 'kernel failure'):
            self.normalize((b'Kernel pa' + self.warning + b'nic: failure\n',))

    def test_incomplete_log_cannot_fabricate_a_complete_prompt(self):
        pending = self.normalize((b'~' + self.warning[:-2],))
        self.assertNotIn(b'~ # ', pending)
        guest_smp.append_console_output(pending, b'\r\n # ')
        self.assertEqual(pending, b'~ # ')


if __name__ == '__main__':
    unittest.main()
