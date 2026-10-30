#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Run only the requested component's tests using its checked-out candidate."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

from workspace import checkout, api, resolve, ORG


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--pull-request', type=int, default=0)
    args = parser.parse_args()
    target = 'main'
    if args.pull_request:
        pull = api(f'repos/{ORG}/{args.component}/pulls/{args.pull_request}')
        topics = [label['name'] for label in pull['labels'] if label['name'].startswith('topic:')]
        if len(topics) > 1:
            raise ValueError('a PR may belong to only one integration topic')
        target = topics[0] if topics else f'{args.component}#{args.pull_request}'
    snapshot = resolve(args.manifest, target)
    if args.component not in snapshot['projects']:
        raise ValueError('component is not registered in the candidate manifest')
    checkout(snapshot, args.directory, {args.component: args.candidate})
    test_component(args.directory, args.component, snapshot['projects'][args.component]['path'])


def test_component(output, name, relative):
    output = output.resolve()
    project = output / 'project.py'
    component = output / relative
    metadata = json.loads((component / 'component.json').read_text())
    needs_workspace = metadata.get('tests_need_workspace', True)
    generated = (output / 'scripts/build.py').is_file()
    environment = dict(os.environ)
    if metadata.get('test_requires_rust', True) or (needs_workspace and generated):
        policy = next((path for path in (output / 'rust-toolchain.toml',
                                        output / 'mk/workspace/rust-toolchain.toml') if path.is_file()), None)
        if policy is None:
            raise ValueError('candidate build tools do not declare a Rust toolchain')
        toolchain = tomllib.loads(policy.read_text())['toolchain']
        command = ['rustup', 'toolchain', 'install', toolchain['channel'], '--profile', 'minimal']
        for rust_component in toolchain['components']:
            command += ['--component', rust_component]
        for target in toolchain['targets']:
            command += ['--target', target]
        subprocess.run(command, check=True)
        environment['RUSTUP_TOOLCHAIN'] = toolchain['channel']
    view = output
    if needs_workspace:
        prepared = subprocess.run([sys.executable, str(project), '--workspace', str(output), 'prepare'],
                                  env=environment, check=True, capture_output=True, text=True)
        lines = prepared.stdout.strip().splitlines()
        if not lines:
            raise ValueError('prepare did not report its execution workspace')
        view = Path(lines[-1]).resolve()
        if not view.is_dir():
            raise ValueError('prepare did not return an existing execution workspace')
    subprocess.run(['sh', str(Path(__file__).resolve().parents[1] / 'ci/check-license-headers.sh')],
                   env={**os.environ, 'HYPER_LICENSE_ROOT': str(component),
                        'HYPER_LICENSE_COMPONENT': name}, check=True)
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=component, text=True).split('\0')
    shells = [str(component / filename) for filename in tracked if filename and
              (filename.endswith('.sh') or Path(filename).name in ('hyper-clang', 'hyper-cargo'))
              and not filename.startswith('legacy/')
              and (name not in ('lib-musl', 'lib-cxx') or filename.startswith('hyper/'))]
    if shells:
        subprocess.run(['shellcheck', '--severity=warning', *shells], cwd=output, check=True)
    if metadata.get('test_requires_rust', True):
        # Native repositories share a Cargo workspace. Select only packages
        # owned by this component so an unrelated formatting error cannot fail it.
        if metadata.get('packages') or name == 'bootstrap':
            for filename in tracked:
                if Path(filename).name != 'Cargo.toml':
                    continue
                manifest = view / relative / filename
                package = tomllib.loads(manifest.read_text()).get('package')
                if package:
                    subprocess.run(['cargo', 'fmt', '--manifest-path', str(manifest),
                                    '-p', package['name'], '--', '--check'], cwd=view, env=environment, check=True)
    if needs_workspace:
        subprocess.run([sys.executable, str(project), '--workspace', str(output),
                        'test', name], env=environment, check=True)
    else:
        commands = metadata.get('tests', [])
        if not commands:
            raise ValueError(f'{name}: module tests are not declared')
        for command in commands:
            subprocess.run([value.replace('{component}', str(component)).replace('{system}', str(output))
                            for value in command], cwd=output, env=environment, check=True)


if __name__ == '__main__':
    main()
