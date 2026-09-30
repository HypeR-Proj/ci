#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Ensure fleet acceptance cannot confuse guest failure with rejected admission."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'fleet_config', Path(__file__).with_name('verify-fleet-config.py'))
fleet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fleet)


class FleetLogTests(unittest.TestCase):
    def test_both_init_supervision_orders_accept_explicit_configuration_rejection(self):
        for init_failure in (
            b'HypeR init: VM fleet configuration rejected\n',
            b"HypeR init: critical service 'vm-manager' terminated: code=1\n",
        ):
            output = (fleet.REJECTED + b' missing image\n' + init_failure
                      + fleet.BOOT_FAILED + b'\n')
            fleet.validate_log('missing-image', output)

    def test_guest_or_unrelated_service_failure_is_not_configuration_rejection(self):
        for output in (
            b'alpine failed\n' + fleet.BOOT_FAILED,
            fleet.REJECTED + b' missing image\n',
            b"HypeR init: critical service 'session' terminated\n" + fleet.BOOT_FAILED,
        ):
            with self.assertRaises(AssertionError):
                fleet.validate_log('malformed', output)

    def test_rejected_batch_cannot_publish_success_or_start_a_guest(self):
        rejected = fleet.REJECTED + b'\n' + fleet.BOOT_FAILED
        for extra in (fleet.CONFIGURED, fleet.GUEST_RUNNING):
            with self.assertRaises(AssertionError):
                fleet.validate_log('missing-image', rejected + b'\n' + extra)

    def test_kernel_failure_cannot_hide_behind_expected_init_failure(self):
        rejected = fleet.REJECTED + b'\n' + fleet.BOOT_FAILED
        for marker in fleet.KERNEL_FAILURES:
            with self.assertRaises(AssertionError):
                fleet.validate_log('malformed', rejected + b'\n' + marker)

    def test_idle_fleet_must_be_acknowledged_without_starting_a_guest(self):
        for case in ('empty', 'no-autostart'):
            fleet.validate_log(case, fleet.CONFIGURED)
            for output in (b'', fleet.CONFIGURED + b'\n' + fleet.GUEST_RUNNING,
                           fleet.CONFIGURED + b'\n' + fleet.BOOT_FAILED):
                with self.assertRaises(AssertionError):
                    fleet.validate_log(case, output)


if __name__ == '__main__':
    unittest.main()
