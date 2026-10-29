#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Run only the requested component's tests using its checked-out candidate."""

import argparse
from pathlib import Path
import subprocess
import sys
import tomllib

from workspace import checkout, project_names, api, resolve, ORG


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--pull-request', type=int, default=0)
    args = parser.parse_args()
    names = project_names(args.manifest)
    if args.component not in names:
        raise ValueError('component is not registered in the source manifest')
    target = 'main'
    if args.pull_request:
        pull = api(f'repos/{ORG}/{args.component}/pulls/{args.pull_request}')
        topics = [label['name'] for label in pull['labels'] if label['name'].startswith('topic:')]
        if len(topics) > 1:
            raise ValueError('a PR may belong to only one integration topic')
        target = topics[0] if topics else f'{args.component}#{args.pull_request}'
    snapshot = resolve(args.manifest, target)
    del snapshot['projects'][args.component]
    checkout(snapshot, args.directory)
    destination = args.directory / args.component
    subprocess.run(['git', 'clone', '--quiet', '--no-local', str(args.candidate), str(destination)], check=True)
    output = args.directory / 'out/system'
    subprocess.run([sys.executable, str(args.directory / 'build/project.py'), '--workspace', str(args.directory),
                    'prepare'], check=True)
    toolchain = tomllib.loads((output / 'rust-toolchain.toml').read_text())['toolchain']
    command = ['rustup', 'toolchain', 'install', toolchain['channel'], '--profile', 'minimal']
    for component in toolchain['components']:
        command += ['--component', component]
    for target in toolchain['targets']:
        command += ['--target', target]
    subprocess.run(command, check=True)
    subprocess.run([sys.executable, str(args.directory / 'build/project.py'), '--workspace', str(args.directory),
                    'test', args.component], check=True)


if __name__ == '__main__':
    main()
