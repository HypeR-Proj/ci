# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import module as runner


class ModuleTests(unittest.TestCase):
    def fixture(self, rust):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        component = root / 'app/owner'
        component.mkdir(parents=True)
        (root / 'rust-toolchain.toml').write_text('[toolchain]\nchannel="test-nightly"\ncomponents=["rustfmt"]\ntargets=[]\n')
        (component / 'component.json').write_text(json.dumps({
            'test_requires_rust': rust, 'packages': ['owner'], 'tests': []}))
        (component / 'probe.sh').write_text('#!/bin/sh\ntrue\n')
        (component / 'Cargo.toml').write_text('[package]\nname="owner"\nversion="0.0.0"\n')
        unrelated = root / 'app/unrelated'
        unrelated.mkdir()
        (unrelated / 'Cargo.toml').write_text('[package]\nname="unrelated"\n')
        subprocess.run(['git', 'init', '-q'], cwd=component, check=True)
        subprocess.run(['git', 'add', '.'], cwd=component, check=True)
        return root, component

    def test_native_formatting_and_test_dispatch_keep_the_repository_identity(self):
        root, component = self.fixture(True)
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root / 'app/owner', text=True)
        with patch.object(runner.subprocess, 'check_output', return_value=tracked), patch.object(runner.subprocess, 'run') as run:
            run.return_value.stdout = str(root) + '\n'
            runner.test_component(root, 'owner', 'app/owner')
        commands = [call.args[0] for call in run.call_args_list]
        formatting = [command for command in commands if command[:2] == ['cargo', 'fmt']]
        self.assertEqual(formatting, [['cargo', 'fmt', '--manifest-path', str(component / 'Cargo.toml'),
                                       '-p', 'owner', '--', '--check']])
        self.assertEqual(commands[-1][-2:], ['test', 'owner'])
        shell_check = next(call for call in run.call_args_list if call.args[0][0] == 'shellcheck')
        self.assertEqual(shell_check.kwargs['cwd'], root)
        self.assertEqual(shell_check.args[0][-1], str(component / 'probe.sh'))
        license_check = next(call for call in run.call_args_list if call.args[0][0] == 'sh')
        self.assertEqual(license_check.kwargs['env']['HYPER_LICENSE_ROOT'], str(component))
        self.assertEqual(license_check.kwargs['env']['HYPER_LICENSE_COMPONENT'], 'owner')

    def test_python_infrastructure_needs_no_rust_toolchain(self):
        root, _ = self.fixture(False)
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root / 'app/owner', text=True)
        with patch.object(runner.subprocess, 'check_output', return_value=tracked), patch.object(runner.subprocess, 'run') as run:
            run.return_value.stdout = str(root) + '\n'
            runner.test_component(root, 'owner', 'app/owner')
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any(command[0] in ('cargo', 'rustup') for command in commands))
        self.assertEqual(commands[-1][-2:], ['test', 'owner'])

    def test_imported_fork_shells_are_not_treated_as_native_adapters(self):
        root, component = self.fixture(False)
        tracked = 'upstream/build.sh\0hyper/probe.sh\0'
        with patch.object(runner.subprocess, 'check_output', return_value=tracked), patch.object(runner.subprocess, 'run') as run:
            run.return_value.stdout = str(root) + '\n'
            runner.test_component(root, 'lib-musl', 'app/owner')
        checks = [call.args[0] for call in run.call_args_list if call.args[0][0] == 'shellcheck']
        self.assertEqual(checks, [['shellcheck', '--severity=warning', str(component / 'hyper/probe.sh')]])

    def test_generated_workspace_formats_view_but_checks_source_ownership(self):
        root, component = self.fixture(True)
        view = root / 'out/workspace/aarch64'
        (view / 'app/owner').mkdir(parents=True)
        (view / 'app/owner/Cargo.toml').write_bytes((component / 'Cargo.toml').read_bytes())
        tracked = 'Cargo.toml\0probe.sh\0'
        with patch.object(runner.subprocess, 'check_output', return_value=tracked), patch.object(runner.subprocess, 'run') as run:
            run.return_value.stdout = str(view) + '\n'
            runner.test_component(root, 'owner', 'app/owner')
        formatting = next(call for call in run.call_args_list if call.args[0][:2] == ['cargo', 'fmt'])
        self.assertIn(str(view / 'app/owner/Cargo.toml'), formatting.args[0])
        license_check = next(call for call in run.call_args_list if call.args[0][0] == 'sh')
        self.assertEqual(license_check.kwargs['env']['HYPER_LICENSE_ROOT'], str(component))

    def test_standalone_module_does_not_prepare_or_compile_system_inputs(self):
        root, component = self.fixture(False)
        (component / 'component.json').write_text(json.dumps({'test_requires_rust': False,
            'tests_need_workspace': False, 'tests': [['python3', '{component}/self-test.py']]}))
        with patch.object(runner.subprocess, 'check_output', return_value=''), patch.object(runner.subprocess, 'run') as run:
            runner.test_component(root, 'owner', 'app/owner')
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any('prepare' in command or command[0] == 'rustup' for command in commands))
        self.assertEqual(commands[-1], ['python3', str(component / 'self-test.py')])

    def test_prepare_bootstraps_its_host_toolchain_before_generating_the_view(self):
        root, _ = self.fixture(False)
        (root / 'scripts').mkdir()
        (root / 'scripts/build.py').write_text('# generated-workspace frontend\n')
        (root / 'mk/workspace').mkdir(parents=True)
        (root / 'rust-toolchain.toml').rename(root / 'mk/workspace/rust-toolchain.toml')
        with patch.object(runner.subprocess, 'check_output', return_value=''), patch.object(runner.subprocess, 'run') as run:
            run.return_value.stdout = str(root) + '\n'
            runner.test_component(root, 'owner', 'app/owner')
        self.assertEqual(run.call_args_list[0].args[0][:3], ['rustup', 'toolchain', 'install'])
        preparation = next(call for call in run.call_args_list if 'prepare' in call.args[0])
        self.assertEqual(preparation.kwargs['env']['RUSTUP_TOOLCHAIN'], 'test-nightly')


if __name__ == '__main__':
    unittest.main()
