#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise CI tool selection and download/installation failure boundaries."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('install_tools', Path(__file__).with_name('install-tools.py'))
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)


class ToolInstallation(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name) / 'archives'
        release = patch.object(tools.platform, 'freedesktop_os_release',
                               return_value={'ID': 'ubuntu', 'VERSION_ID': '24.04'})
        release.start()
        self.addCleanup(release.stop)
        identity = patch.object(tools.os, 'geteuid', return_value=1000)
        identity.start()
        self.addCleanup(identity.stop)

    def test_available_tools_need_no_package_manager(self):
        with patch.object(tools.shutil, 'which', return_value='/usr/local/bin/tool'), \
                patch.object(tools.subprocess, 'run') as run:
            tools.install(['cmake', 'llvm'], self.cache)
            run.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_every_command_in_a_package_must_be_available(self):
        with patch.object(tools.shutil, 'which', side_effect=lambda name: name != 'llvm-ranlib'):
            self.assertEqual(tools.missing_packages(['cmake', 'llvm', 'llvm']), ['llvm'])

    def test_erofs_deployments_install_only_when_builder_is_missing(self):
        with patch.object(tools.shutil, 'which', return_value=None):
            self.assertEqual(tools.missing_packages(['erofs-utils']), ['erofs-utils'])
        with patch.object(tools.shutil, 'which', side_effect=lambda name: name == 'mkfs.erofs'), \
                patch.object(tools.subprocess, 'run') as run:
            tools.install(['erofs-utils'], self.cache)
            run.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_unknown_package_cannot_reach_sudo(self):
        with patch.object(tools.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'unknown CI tool'):
                tools.install(['--allow-unauthenticated'], self.cache)
            run.assert_not_called()

    def execute(self, results):
        calls = []
        results = iter(results)

        def run(command, **kwargs):
            calls.append((command, kwargs, (self.cache.parent / 'hyper-tools.list').read_text()))
            result = subprocess.CompletedProcess(command, next(results))
            if kwargs.get('check'):
                result.check_returncode()
            return result

        with patch.object(tools.shutil, 'which', return_value=None), \
                patch.object(tools.subprocess, 'run', side_effect=run):
            tools.install(['qemu-system-misc'], self.cache)
        return calls

    def test_download_deadline_falls_back_without_interrupting_dpkg(self):
        calls = self.execute([0, 124, 0, 0, 0])
        self.assertEqual(len(calls), 5)
        self.assertIn('http://archive.ubuntu.com/ubuntu', calls[0][2])
        self.assertIn('http://azure.archive.ubuntu.com/ubuntu', calls[2][2])
        for command, _, sources in calls[:-1]:
            self.assertEqual(command[:3], ['sudo', 'timeout', '--kill-after=10s'])
            self.assertIn('signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg', sources)
            self.assertIn('noble-security', sources)
            if 'install' in command:
                self.assertIn('--download-only', command)
        install, options, _ = calls[-1]
        self.assertEqual(install[:2], ['sudo', 'apt-get'])
        self.assertIn('--no-download', install)
        self.assertNotIn('timeout', install)
        self.assertTrue(options['check'])

    def test_failed_index_update_never_downloads_from_that_mirror(self):
        calls = self.execute([100, 0, 0, 0])
        self.assertIn('update', calls[0][0])
        self.assertIn('update', calls[1][0])
        self.assertIn('--download-only', calls[2][0])

    def test_cached_archives_still_use_authenticated_indexes(self):
        self.cache.mkdir()
        (self.cache / 'existing.deb').write_bytes(b'cached archive')
        calls = self.execute([0, 0, 0])
        self.assertIn('update', calls[0][0])
        self.assertIn('--download-only', calls[1][0])
        self.assertIn(f'Dir::Cache::archives={self.cache.resolve()}', calls[1][0])
        self.assertIn('--no-download', calls[2][0])

    def test_download_failure_does_not_start_installation(self):
        with self.assertRaisesRegex(RuntimeError, 'both ubuntu mirrors'):
            self.execute([0, 100, 0, 100])

    def test_dpkg_failure_is_reported_without_network_retry(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute([0, 0, 100])

    def test_other_distributions_cannot_use_noble_packages(self):
        with patch.object(tools.shutil, 'which', return_value=None), \
                patch.object(tools.platform, 'freedesktop_os_release', return_value={'ID': 'debian'}), \
                patch.object(tools.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Ubuntu 24.04'):
                tools.install(['qemu-system-arm'], self.cache)
            run.assert_not_called()

    def test_debian_container_uses_its_keyring_and_no_sudo(self):
        with patch.object(tools.platform, 'freedesktop_os_release',
                          return_value={'ID': 'debian', 'VERSION_ID': '13'}), \
                patch.object(tools.os, 'geteuid', return_value=0):
            calls = self.execute([0, 0, 0])
        for command, _, sources in calls:
            self.assertNotIn('sudo', command)
            self.assertIn('signed-by=/usr/share/keyrings/debian-archive-keyring.gpg', sources)
            self.assertIn('http://deb.debian.org/debian trixie main', sources)
            self.assertIn('trixie-updates main', sources)
            self.assertIn('http://deb.debian.org/debian-security trixie-security main', sources)
            self.assertNotIn('ubuntu', sources)
        self.assertEqual(calls[-1][0][0], 'apt-get')

    def test_debian_fallback_keeps_release_and_security_repositories(self):
        with patch.object(tools.platform, 'freedesktop_os_release',
                          return_value={'ID': 'debian', 'VERSION_ID': '13'}):
            calls = self.execute([100, 0, 0, 0])
        self.assertIn('http://ftp.debian.org/debian trixie main', calls[1][2])
        self.assertIn('http://security.debian.org/debian-security trixie-security main', calls[1][2])

    def test_old_debian_cannot_install_trixie_packages(self):
        with patch.object(tools.shutil, 'which', return_value=None), \
                patch.object(tools.platform, 'freedesktop_os_release',
                             return_value={'ID': 'debian', 'VERSION_ID': '12'}), \
                patch.object(tools.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Debian 13'):
                tools.install(['qemu-system-arm'], self.cache)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
