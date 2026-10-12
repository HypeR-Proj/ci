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

    def test_glob_members_still_enforce_dependency_policy(self):
        self.write('app/Cargo.toml', '[workspace]\nmembers = ["*"]\n'
                   'exclude = [".cargo", "target"]\n')
        self.write('app/README.md', 'not a package')
        self.write('app/.cargo/config.toml', '')
        self.write('app/target/Cargo.toml', 'not a package manifest')
        workspace.check_apps(self.root)
        self.write('app/new-tool/Cargo.toml', '[package]\nname = "new-tool"\n'
                   '[dependencies]\nhyper-sys = "=0.0.0"\n')
        with self.assertRaisesRegex(ValueError, 'raw syscalls'):
            workspace.check_apps(self.root)

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
                   'os = {package = "hyper-os", path = "../../stdlib/rust/hyper-os"}\n')
        with self.assertRaisesRegex(ValueError, 'installed SDK'):
            workspace.check_apps(self.root)

    def test_member_alias_resolves_package_identity(self):
        self.write('app/tool/Cargo.toml', '[package]\nname = "tool"\n[dependencies]\n'
                   'shared = {package = "policy", path = "../policy"}\n')
        workspace.check_apps(self.root)
        self.write('app/tool/Cargo.toml', '[package]\nname = "tool"\n[dependencies]\n'
                   'shared = {package = "wrong", path = "../policy"}\n')
        with self.assertRaisesRegex(ValueError, 'own Native workspace'):
            workspace.check_apps(self.root)

    def test_member_cannot_escape_through_symlink(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'app/escape').symlink_to(outside, target_is_directory=True)
        self.write('app/Cargo.toml', '[workspace]\nmembers = ["escape"]\n')
        with self.assertRaisesRegex(ValueError, 'escapes app'):
            workspace.check_apps(self.root)

    def test_external_members_obey_the_same_sdk_boundary(self):
        for member, sdk_path in [('lib/args', '../../stdlib/rust/hyper-os'),
                                 ('stdlib/rust-std', '../rust/hyper-os')]:
            self.write('app/Cargo.toml', '[workspace]\n'
                       f'members = ["tool", "policy", "../{member}"]\n')
            self.write(f'{member}/Cargo.toml', '[package]\nname = "external"\n')
            self.write('app/tool/Cargo.toml', '[package]\nname = "tool"\n[dependencies]\n'
                       f'external = {{path = "../../{member}"}}\n')
            workspace.check_apps(self.root)
            self.write(f'{member}/Cargo.toml', '[package]\nname = "external"\n[dependencies]\n'
                       f'os = {{package = "hyper-os", path = "{sdk_path}"}}\n')
            with self.subTest(member=member), self.assertRaisesRegex(ValueError, 'installed SDK'):
                workspace.check_apps(self.root)

    def test_shared_library_cannot_escape_through_symlink(self):
        (self.root / 'outside').mkdir()
        (self.root / 'lib').symlink_to(self.root / 'outside', target_is_directory=True)
        self.write('lib/args/Cargo.toml', '[package]\nname = "args"\n')
        self.write('app/Cargo.toml', '[workspace]\nmembers = ["../lib/args"]\n')
        with self.assertRaisesRegex(ValueError, 'escapes app'):
            workspace.check_apps(self.root)

    def bootstrap(self):
        self.write('bootstrap/stage1/Cargo.toml', '[workspace]\nmembers = ["init", "private"]\n'
                   '[workspace.dependencies]\nservice = {package="boot-service", path="private"}\n')
        self.write('bootstrap/stage1/init/Cargo.toml', '[package]\nname="boot-init"\n'
                   '[dependencies]\nservice.workspace=true\nhyper-os="=0.0.0"\n')
        self.write('bootstrap/stage1/private/Cargo.toml', '[package]\nname="boot-service"\n'
                   '[dependencies]\nserde="1"\n')
        self.write('lib/service/Cargo.toml', '[package]\nname="system-service"\n')

    def test_bootstrap_can_share_substrate_and_third_party(self):
        self.bootstrap()
        workspace.check_bootstrap(self.root)

    def test_bootstrap_rejects_transitive_system_dependencies_in_all_contexts(self):
        self.bootstrap()
        for section in ('dependencies', 'build-dependencies', 'dev-dependencies',
                        'target.\'cfg(target_os="hyper")\'.dependencies'):
            for spec in ('{package="system-service", path="../../../lib/service"}',
                         '{package="system-service", version="0.0.0"}'):
                with self.subTest(section=section, spec=spec):
                    self.write('bootstrap/stage1/private/Cargo.toml',
                               '[package]\nname="boot-service"\n'
                               f'[{section}]\nindirect={spec}\n')
                    with self.assertRaisesRegex(ValueError, 'system library'):
                        workspace.check_bootstrap(self.root)

    def test_bootstrap_rejects_path_escape_and_shared_delivery(self):
        self.bootstrap()
        self.write('bootstrap/stage1/private/Cargo.toml', '[package]\nname="boot-service"\n'
                   '[dependencies]\nargs={path="../../../lib/args"}\n')
        with self.assertRaisesRegex(ValueError, 'installed SDK'):
            workspace.check_bootstrap(self.root)
        self.write('app/Cargo.toml', '[workspace]\nmembers=["../bootstrap/stage1/init"]\n')
        with self.assertRaisesRegex(ValueError, 'escapes app'):
            workspace.check_apps(self.root)

    def test_stdlib_cannot_smuggle_system_policy_into_bootstrap(self):
        self.bootstrap()
        self.write('stdlib/rust/Cargo.toml', '[workspace]\nmembers=["safe"]\n')
        self.write('stdlib/rust/safe/Cargo.toml', '[package]\nname="safe"\n')
        workspace.check_stdlib(self.root)
        self.write('stdlib/rust/safe/Cargo.toml', '[package]\nname="safe"\n'
                   '[dependencies]\nprivate={path="../../../bootstrap/stage1/private"}\n')
        with self.assertRaisesRegex(ValueError, 'stdlib substrate'):
            workspace.check_stdlib(self.root)

    def test_vendored_dependency_is_not_a_boundary_bypass(self):
        self.bootstrap()
        self.write('bootstrap/stage1/private/Cargo.toml', '[package]\nname="boot-service"\n'
                   '[dependencies]\nparser={path="../../../third_party/parser"}\n')
        self.write('third_party/parser/Cargo.toml', '[package]\nname="parser"\n'
                   '[build-dependencies]\nhelper={package="system-service", version="0"}\n')
        with self.assertRaisesRegex(ValueError, 'system library'):
            workspace.check_bootstrap(self.root)

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
