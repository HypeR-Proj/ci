#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Keep bootstrap retirement acceptance strict about ownership and backing."""

import importlib.util
from itertools import repeat
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    'boot_system', Path(__file__).with_name('verify-boot-system.py'))
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)


class RetirementEvidenceTests(unittest.TestCase):
    def check(self, *, extra_process=b'', boot_entries=b'', reserved=4096):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'console.log'
            log.write_bytes(
                b'HypeR: memory (kernel initialized): 1024 MiB RAM, 10 reserved pages\n'
                b'HypeR init: bootstrap retired; boot archive detached\n')
            processes = b''.join(
                b'process 0x01 - ' + name + b' running threads=1 pending=0\n'
                for name in (b'console-raw', b'io-runtime', b'fs-backend', b'init'))
            outputs = {
                'ls /boot/init': b'ls: /boot/init: not found\n',
                'ls -1 /boot': boot_entries,
                'ps --no-headers': processes + extra_process,
            }
            reservations = iter(reserved) if isinstance(reserved, tuple) else repeat(reserved)

            def run(command, expected=None, **kwargs):
                self.assertEqual(kwargs.get('fails', False), command == 'ls /boot/init')
                if command == 'free --bytes':
                    return f'Reserved: {next(reservations)} B\n'.encode()
                return outputs[command]

            boot.retired_bootstrap(Mock(), run, log)

    def test_independent_formal_owners_and_reclaimed_disk_archive_pass(self):
        self.check()

    def test_retirement_banner_cannot_hide_a_live_predecessor(self):
        for name in (b'init', b'fallback-sh', b'io-runtime', b'fs-backend', b'console-raw'):
            with self.subTest(name=name), self.assertRaises(AssertionError):
                self.check(extra_process=b'process 0x02 - ' + name + b' running threads=1\n')

    def test_retired_process_record_does_not_count_as_a_live_predecessor(self):
        self.check(extra_process=b'process 0x02 - init retired threads=0\n')

    def test_detached_archive_cannot_remain_accessible_by_path(self):
        with self.assertRaisesRegex(AssertionError, 'remains reachable'):
            self.check(boot_entries=b'svc/\n')

    def test_archive_reclamation_is_required(self):
        with patch.object(boot.time, 'monotonic', side_effect=(0, 0, 31)), \
                self.assertRaisesRegex(AssertionError, 'reservation did not shrink'):
            self.check(reserved=40960)

    def test_last_retired_image_may_release_after_namespace_detachment(self):
        with patch.object(boot.time, 'monotonic', side_effect=(0, 0, 1)):
            self.check(reserved=(40960, 4096))


class RootHandoffEvidenceTests(unittest.TestCase):
    def test_formal_programs_live_at_root_without_system_alias(self):
        root = b'bin/\netc/\nlib@\nlib64/\nsvc/\nboot/\ndata/\ntmp/\nvm/\n'
        boot.check_root_layout(root)
        with self.assertRaises(AssertionError):
            boot.check_root_layout(root + b'system/\n')
        with self.assertRaises(AssertionError):
            boot.check_root_layout(b'boot/\nsystem/\ndata/\ntmp/\n')

    def test_root_is_switched_before_candidate_adopts_owners(self):
        markers = (
            b'EROFS mounted at /system',
            b'HypeR bootstrap-stage1-init: provisional system root active; preparing successor',
            b'HypeR init: core supervision adopted',
            b'FAT mounted at /data', b'HypeR init: VM fleet configured',
            b'HypeR init: bootstrap retired; boot archive detached',
        )
        boot.check_handoff_order(b'\n'.join(markers))
        with self.assertRaises(AssertionError):
            boot.check_handoff_order(b'\n'.join((markers[0], markers[2], markers[1], *markers[3:])))

    def test_recovery_requires_namespace_rollback_before_prompt(self):
        markers = (
            b'HypeR bootstrap-stage1-init: provisional system root active; preparing successor',
            b'HypeR bootstrap-stage1-init: system handoff rejected; bootstrap root restored',
            b'HypeR bootstrap-stage1-fallback-sh: recovery console ready',
        )
        boot.check_rollback_order(b'\n'.join(markers))
        with self.assertRaises(AssertionError):
            boot.check_rollback_order(b'\n'.join((markers[0], markers[2], markers[1])))
        with self.assertRaises(AssertionError):
            boot.check_rollback_order(b'\n'.join((markers[0], markers[2])))
        for commit in (b'core supervision transferred', b'core supervision adopted'):
            with self.assertRaises(AssertionError):
                boot.check_rollback_order(b'\n'.join((*markers, commit)))


class ProviderRejectionEvidenceTests(unittest.TestCase):
    def evidence(self, case):
        common = (
            b'HypeR init: core supervision adopted',
            b'HypeR init: provider adoption failed: bootstrap retirement disabled; '
            b'replacement preparation failed; bootstrap provider retained',
            b'HypeR bootstrap-stage1-fs-backend: adoption preparation failed; bootstrap mounts retained: closed',
            b'HypeR session: console ready',
        )
        if case == 'missing-fs-erofs':
            common += (b'HypeR io-runtime: running backend adopted',
                       b'EROFS recognized for /, but filesystem worker is unavailable')
        return b'\n'.join(common)

    def test_expected_preparation_failures_preserve_formal_session(self):
        for case in boot.PROVIDER_REJECTIONS:
            boot.check_provider_rejection(case, self.evidence(case))

    def test_wrong_failure_boundary_or_retirement_cannot_pass(self):
        for case in boot.PROVIDER_REJECTIONS:
            evidence = self.evidence(case)
            for extra in (b'HypeR init: bootstrap retired; boot archive detached',
                          b'HypeR bootstrap-stage1-fallback-sh: recovery console ready',
                          b'HypeR fs-backend: filesystem ownership adopted'):
                with self.subTest(case=case, extra=extra), self.assertRaises(AssertionError):
                    boot.check_provider_rejection(case, evidence + b'\n' + extra)
            with self.assertRaises(AssertionError):
                boot.check_provider_rejection(case, evidence.replace(
                    b'bootstrap-stage1-fs-backend: adoption preparation failed;', b'unrelated:'))
        with self.assertRaises(AssertionError):
            boot.check_provider_rejection('missing-fs-erofs', self.evidence('missing-io-runtime'))
        with self.assertRaises(AssertionError):
            boot.check_provider_rejection('missing-io-runtime', self.evidence('missing-fs-erofs'))

    def test_fault_fixture_only_changes_its_new_system_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'original'
            (source / 'svc').mkdir(parents=True)
            for name in boot.PROVIDER_REJECTIONS.values():
                (source / 'svc' / name).write_bytes(b'original executable')
            for case, missing in boot.PROVIDER_REJECTIONS.items():
                output = root / case
                with patch.object(boot.subprocess, 'run') as pack:
                    boot.prepare_provider_rejection([
                        '--case', case, '--system-root', str(source),
                        '--board', str(root / 'board.json'), '--output', str(output),
                        '--mkfs-erofs', '/test/mkfs.erofs', '--artifact', 'alpine=/test/alpine.itb',
                    ])
                self.assertTrue((source / 'svc' / missing).is_file())
                self.assertFalse((output / 'system-root/svc' / missing).exists())
                invocation = pack.call_args.args[0]
                self.assertIn(str(output / 'disk.img'), invocation)
                self.assertIn('alpine=/test/alpine.itb', invocation)
                self.assertNotIn('--replace', invocation)


if __name__ == '__main__':
    unittest.main()
