#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Build the selected I/O VM source for system verification."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess

from workspace import checkout_one


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--platform', choices=['qemu', 'rpi5'], default='qemu')
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text())
    args.workspace.mkdir(parents=True, exist_ok=True)
    checkout_one(args.workspace, 'io-vm', snapshot['projects']['io-vm'])
    root = args.workspace / 'io-vm'
    subprocess.run(['python3', 'tools/libc-materials.py', '--output', 'target/io-vm/libc-materials'], cwd=root, check=True)
    subprocess.run(['make', 'base', f'PLATFORM={args.platform}',
                    'CROSS_COMPILE=aarch64-linux-gnu-', 'JOBS=4'], cwd=root, check=True)
    subprocess.run(['python3', 'tools/export-package.py', '--platform', args.platform,
                    '--output', str(args.output)], cwd=root, check=True)
    (args.output / 'candidate.json').write_text(json.dumps(snapshot, indent=2) + '\n')


if __name__ == '__main__':
    main()
