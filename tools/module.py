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
    subprocess.run([sys.executable, str(project), '--workspace', str(output), 'prepare'], check=True)
    component = output / relative
    metadata = json.loads((component / 'component.json').read_text())
    subprocess.run(['sh', str(Path(__file__).resolve().parents[1] / 'ci/check-license-headers.sh')],
                   env={**os.environ, 'HYPER_LICENSE_ROOT': str(component),
                        'HYPER_LICENSE_COMPONENT': name}, check=True)
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=component, text=True).split('\0')
    shells = [str(component / name) for name in tracked if name and
              (name.endswith('.sh') or Path(name).name in ('hyper-clang', 'hyper-cargo'))
              and not name.startswith('legacy/')]
    if shells:
        subprocess.run(['shellcheck', '--severity=warning', *shells], check=True)
    if metadata.get('test_requires_rust', True):
        toolchain = tomllib.loads((output / 'rust-toolchain.toml').read_text())['toolchain']
        command = ['rustup', 'toolchain', 'install', toolchain['channel'], '--profile', 'minimal']
        for rust_component in toolchain['components']:
            command += ['--component', rust_component]
        for target in toolchain['targets']:
            command += ['--target', target]
        subprocess.run(command, check=True)
        # Native repositories share a Cargo workspace. Select only packages
        # owned by this component so an unrelated formatting error cannot fail it.
        if metadata.get('packages') or name == 'bootstrap':
            for filename in tracked:
                if Path(filename).name != 'Cargo.toml':
                    continue
                manifest = component / filename
                package = tomllib.loads(manifest.read_text()).get('package')
                if package:
                    subprocess.run(['cargo', 'fmt', '--manifest-path', str(manifest),
                                    '-p', package['name'], '--', '--check'], cwd=output, check=True)
    subprocess.run([sys.executable, str(project), '--workspace', str(output),
                    'test', name], check=True)


if __name__ == '__main__':
    main()
