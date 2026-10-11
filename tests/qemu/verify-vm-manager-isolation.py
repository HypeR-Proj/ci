#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Inject business-manager failures while exercising real Native disk services.

Uses a previously built split deployment. Only temporary copies of its system
tree and a new test disk are modified; the supplied images remain unchanged.
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = {'exit': 0, 'closed-reply': 1, 'invalid-reply': 2, 'configured-exit': 3}
CASES = ('absent', 'missing', *FIXTURES, 'no-config')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    for name in ('image', 'initramfs', 'board', 'system-root', 'sdk', 'output', 'mkfs-erofs'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--artifact', action='append', default=[], metavar='NAME=PATH')
    parser.add_argument('--cases', nargs='+', choices=CASES, default=CASES)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    # A temporary directory also makes repeated invocations independent of old
    # fault images. Logs survive under --output even when a case fails.
    with tempfile.TemporaryDirectory(prefix='manager-isolation-', dir=args.output) as temporary:
        work = Path(temporary)
        fixtures = {}
        for case in args.cases:
            if case not in FIXTURES:
                continue
            fixture = work / case
            subprocess.run([
                str(args.sdk / 'bin/hyper-clang'), '-std=c17', '-Wall', '-Wextra', '-Werror',
                f'-DVM_PROVISION_TEST={FIXTURES[case]}',
                str(ROOT / 'tests/native/vm-provisioning.c'), '-o', str(fixture),
            ], check=True)
            fixtures[case] = fixture
        system = work / 'system-root'
        shutil.copytree(args.system_root, system, symlinks=True)
        manifest_path = system / 'etc/hyper/services.json'
        original = manifest_path.read_text()
        for case in args.cases:
            manifest = json.loads(original)
            manager = system / 'svc/vm-manager'
            if manager.exists():
                manager.unlink()
            if case == 'absent':
                manifest['services'] = [item for item in manifest['services']
                                        if item['name'] != 'vm-manager']
                manifest.pop('virtual-machines', None)
            elif case == 'no-config':
                manifest.pop('virtual-machines', None)
                shutil.copy2(args.system_root / 'svc/vm-manager', manager)
            elif case in fixtures:
                shutil.copy2(fixtures[case], manager)
            manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
            # Missing executable is intentional; verify runtime degradation
            # rather than sending the deliberately incomplete tree to a packer
            # which checks executable presence in the bootstrap archive.
            disk = work / 'disk.img'
            pack = [sys.executable, '-B', str(ROOT / 'scripts/pack-board-image.py'),
                    '--board', str(args.board), '--output', str(disk), '--replace',
                    '--system-root', str(system), '--mkfs-erofs', str(args.mkfs_erofs)]
            for artifact in args.artifact:
                pack.extend(['--artifact', artifact])
            subprocess.run(pack, check=True)
            subprocess.run([
                sys.executable, '-B', str(Path(__file__).with_name('verify-boot-system.py')),
                '--qemu', args.qemu, '--image', str(args.image),
                '--initramfs', str(args.initramfs), '--board', str(args.board),
                '--disk', str(disk), '--manager-case', case,
                '--log', str(args.output / f'{case}.log'),
            ], check=True)


if __name__ == '__main__':
    main()
