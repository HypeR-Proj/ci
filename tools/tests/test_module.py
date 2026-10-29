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
            runner.test_component(root, 'owner', 'app/owner')
        commands = [call.args[0] for call in run.call_args_list]
        formatting = [command for command in commands if command[:2] == ['cargo', 'fmt']]
        self.assertEqual(formatting, [['cargo', 'fmt', '--manifest-path', str(component / 'Cargo.toml'),
                                       '-p', 'owner', '--', '--check']])
        self.assertEqual(commands[-1][-2:], ['test', 'owner'])
        license_check = next(call for call in run.call_args_list if call.args[0][0] == 'sh')
        self.assertEqual(license_check.kwargs['env']['HYPER_LICENSE_ROOT'], str(component))
        self.assertEqual(license_check.kwargs['env']['HYPER_LICENSE_COMPONENT'], 'owner')

    def test_python_infrastructure_needs_no_rust_toolchain(self):
        root, _ = self.fixture(False)
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root / 'app/owner', text=True)
        with patch.object(runner.subprocess, 'check_output', return_value=tracked), patch.object(runner.subprocess, 'run') as run:
            runner.test_component(root, 'owner', 'app/owner')
        commands = [call.args[0] for call in run.call_args_list]
        self.assertFalse(any(command[0] in ('cargo', 'rustup') for command in commands))
        self.assertEqual(commands[-1][-2:], ['test', 'owner'])


if __name__ == '__main__':
    unittest.main()
