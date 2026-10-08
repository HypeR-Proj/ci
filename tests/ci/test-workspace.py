#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise dependency policy with manifests, including aliases and cfg tables."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('workspace', Path(__file__).with_name('check-workspace.py'))
workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='hyper-workspace-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.write('app/Cargo.toml', '[workspace]\nmembers = ["tool", "policy"]\n'
                   '[workspace.dependencies]\nhyper-os = "=0.0.0"\n')
        self.write('app/tool/Cargo.toml', '[package]\nname = "tool"\n[dependencies]\n'
                   'hyper-os.workspace = true\npolicy = {path = "../policy"}\n')
        self.write('app/policy/Cargo.toml', '[package]\nname = "policy"\n')
        self.write('kernel/core/Cargo.toml', '[dependencies]\n'
                   'abi = {package = "hyper-abi", path = "../../sdk/abi"}\n')

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_valid_declarations_and_comments(self):
        # An explanation or a URL is not a dependency declaration.
        with (self.root / 'app/tool/Cargo.toml').open('a') as stream:
            stream.write('# hyper-sys is intentionally unavailable here\n')
        workspace.check_apps(self.root)
        workspace.check_abi(self.root)

    def test_aliased_raw_syscalls_in_every_dependency_context(self):
        for section in ('dependencies', 'dev-dependencies', 'build-dependencies',
                        'target.\'cfg(target_os = "hyper")\'.dependencies',
                        'patch.crates-io'):
            with self.subTest(section=section):
                self.write('app/tool/Cargo.toml', '[package]\nname = "tool"\n'
                           f'[{section}]\nraw = {{package = "hyper-sys", version = "0"}}\n')
                with self.assertRaisesRegex(ValueError, 'raw syscalls'):
                    workspace.check_apps(self.root)

    def test_inherited_raw_syscall_dependency(self):
        self.write('app/Cargo.toml', '[workspace]\nmembers = ["tool", "policy"]\n'
                   '[workspace.dependencies]\nraw = {package = "hyper-sys", version = "0"}\n')
        with self.assertRaisesRegex(ValueError, 'raw syscalls'):
            workspace.check_apps(self.root)

    def test_sdk_path_cannot_bypass_installed_sdk(self):
        self.write('app/tool/Cargo.toml', '[package]\nname = "tool"\n[dependencies]\n'
                   'os = {package = "hyper-os", path = "../../sdk/rust/hyper-os"}\n')
        with self.assertRaisesRegex(ValueError, 'installed SDK'):
            workspace.check_apps(self.root)

    def test_member_alias_resolves_package_identity(self):
        self.write('app/tool/Cargo.toml', '[package]\nname = "tool"\n[dependencies]\n'
                   'shared = {package = "policy", path = "../policy"}\n')
        workspace.check_apps(self.root)
        self.write('app/tool/Cargo.toml', '[package]\nname = "tool"\n[dependencies]\n'
                   'shared = {package = "wrong", path = "../policy"}\n')
        with self.assertRaisesRegex(ValueError, 'workspace member'):
            workspace.check_apps(self.root)

    def test_member_cannot_escape_through_symlink(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'app/escape').symlink_to(outside, target_is_directory=True)
        self.write('app/Cargo.toml', '[workspace]\nmembers = ["escape"]\n')
        with self.assertRaisesRegex(ValueError, 'escapes app'):
            workspace.check_apps(self.root)

    def test_kernel_cannot_use_published_or_foreign_abi(self):
        for declaration in ('"=0.0.0"', '{path = "../foreign"}'):
            self.write('kernel/core/Cargo.toml', f'[dependencies]\nhyper-abi = {declaration}\n')
            with self.assertRaisesRegex(ValueError, 'in-tree hyper-abi'):
                workspace.check_abi(self.root)

    def test_index_uses_modes_and_complete_paths(self):
        workspace.check_index(b'100644 abc 0\tfile with spaces.rs\0')
        for entry in (b'160000 abc 0\tmodule\0', b'100644 abc 0\tsdk/components.lock\0'):
            with self.assertRaises(ValueError):
                workspace.check_index(entry)


if __name__ == '__main__':
    unittest.main()
