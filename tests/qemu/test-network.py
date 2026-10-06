#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Keep backend retirement proofs distinct from shell progress and log noise."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location(
    'verify_network', Path(__file__).with_name('verify-network.py'))
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)

RELEASE = b'HypeR IO VM: HypeR I/O [hbr0]: reply RELEASE_MEMORY: ok\r\n'


class NetworkLogTests(unittest.TestCase):
    def test_stop_acknowledgement_and_prompt_can_split_release(self):
        for fragment in (b'accepted\n', b'hyper-sh$ ', b'accepted\nhyper-sh$ '):
            for offset in range(len(RELEASE) - 1):
                output = RELEASE[:offset] + fragment + RELEASE[offset:]
                with self.subTest(fragment=fragment, offset=offset):
                    self.assertEqual(network.release_acknowledgements(output), 1)
        output = RELEASE.replace(b'RELEASE', b'REhyper-sh$ LEASE')
        self.assertEqual(network.release_acknowledgements(output), 1)

    def test_only_complete_successful_appliance_records_count(self):
        for length in range(len(RELEASE)):
            self.assertEqual(network.release_acknowledgements(RELEASE[:length]), 0)
        for output in (
            b'reply RELEASE_MEMORY: ok\n',
            RELEASE.replace(b'reply', b'request'),
            RELEASE.replace(b': ok', b': failed'),
            RELEASE.replace(b': ok', b': okay'),
            RELEASE.replace(b'RELEASE', b'RENAME STATE\nalpine stopped\nLEASE'),
            b'alpine stopped\n',
        ):
            with self.subTest(output=output):
                self.assertEqual(network.release_acknowledgements(output), 0)
        self.assertEqual(network.release_acknowledgements(RELEASE * 4), 4)

    def test_host_records_are_reassembled_without_hiding_failure(self):
        log = b'<6>[ 12.5] HypeR: host diagnostic\n'
        self.assertEqual(network.release_acknowledgements(
            RELEASE.replace(b'RELEASE', b'RE' + log + b'LEASE')), 1)
        with self.assertRaises(RuntimeError):
            network.release_acknowledgements(RELEASE + b'HypeR: fatal\n')

    def test_missing_failed_and_extra_release_records_do_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            logfile = Path(directory) / 'network.log'
            scenario = network.Scenario(Mock(logfile=logfile), 'http://unused', 'unused')
            for output in (b'', RELEASE * 3, RELEASE * 3 + RELEASE.replace(b': ok', b': failed')):
                logfile.write_bytes(output)
                with self.assertRaises(TimeoutError):
                    scenario.retired(4, timeout=0)
            logfile.write_bytes(RELEASE * 5)
            with self.assertRaises(RuntimeError):
                scenario.retired(4, timeout=0)
            logfile.write_bytes(RELEASE * 4)
            scenario.retired(4, timeout=0)

    def test_stop_waits_for_release_before_querying_manager(self):
        session = Mock()
        scenario = network.Scenario(session, 'http://unused', 'unused')
        events = []
        scenario.send = lambda command: events.append(('send', command))
        session.await_text.side_effect = lambda pattern, **_: (
            events.append(('await', pattern)) or b'accepted\n')
        scenario.retired = lambda expected: events.append(('release', expected))
        scenario.state = lambda name, state: events.append(('state', name, state))
        scenario.stop({'name': 'alpine'})
        scenario.stop({'name': 'alpine-net'})
        self.assertEqual(scenario.releases, 2)
        for index, name in enumerate(('alpine', 'alpine-net')):
            stop = events[index * 5:(index + 1) * 5]
            self.assertEqual(stop[0], ('send', f'vmm stop {name}\n'.encode()))
            self.assertEqual(stop[1][0], 'await')
            self.assertEqual(stop[2], ('await', rb'hyper-sh\$ '))
            self.assertEqual(stop[3], ('release', index + 1))
            self.assertEqual(stop[4], ('state', name, b'stopped'))


if __name__ == '__main__':
    unittest.main()
