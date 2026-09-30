#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Manually measure fresh HypeR/Alpine starts; never impose CI timing gates."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/qemu'))
from session import Session


def boot(args, index):
    folder = args.output / str(index)
    folder.mkdir()
    # This warms only the host file cache, not either guest's filesystem cache.
    with (args.fixture / 'base.img').open('rb') as stream:
        disk_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
    command = [args.qemu, '-machine', 'virt-11.1,virtualization=on,gic-version=3',
               '-accel', 'tcg,thread=multi', '-cpu', 'max', '-smp', '4', '-m', '1G',
               '-nodefaults', '-display', 'none', '-nic', 'none', '-no-reboot',
               '-kernel', str(args.image), '-initrd', str(args.initramfs),
               '-append', 'earlycon=pl011,mmio32,0x09000000',
               '-global', 'virtio-mmio.force-legacy=false', '-snapshot',
               '-drive', f'if=none,id=disk,format=raw,file={str(args.fixture / "base.img").replace(",", ",,")},cache=writeback',
               '-device', 'virtio-scsi-device,id=scsi,iommu_platform=on',
               '-device', 'scsi-hd,drive=disk,bus=scsi.0,scsi-id=0,lun=0',
               '-serial', 'stdio', '-monitor', 'none']
    (folder / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
    start = time.monotonic()
    with Session(command, folder / 'serial.log', failures=(b'HypeR: fatal', b'HypeR KERNEL PANIC',
                 b'Kernel panic', b'HypeR vm-runtime: failed', b'HypeR io-runtime: failed')) as vm:
        output = vm.await_text(rb'HypeR vm-runtime: vCPU start submitted in ([0-9]+\.[0-9]+) ms \(from main\)\n', timeout=240)
        submitted = time.monotonic() - start
        duration = re.search(rb'vCPU start submitted in ([0-9]+\.[0-9]+) ms', output)
        vm.send(b'vmm console alpine\n')
        vm.await_text(rb'HypeR guest: Linux userspace is running\n', timeout=180)
        vm.await_text(rb'~ # ', timeout=60)
        ready = time.monotonic() - start
        vm.send(b"echo LOCAL-''BOOT-PASS\n")
        vm.await_text(rb'\nLOCAL-BOOT-PASS\n', timeout=30)
    result = {'boot': index, 'disk_sha256': disk_sha256,
              'cold_start_to_vcpu_seconds': submitted,
              'runtime_main_to_vcpu_ms': float(duration[1]),
              'cold_start_to_guest_shell_seconds': ready,
              'guest_shell': 'passed',
              'profile_lines': [line.decode() for line in output.splitlines() if b'startup profile:' in line]}
    (folder / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    return result


def main():
    if any(os.environ.get(name, '').lower() in ('1', 'true') for name in ('CI', 'GITHUB_ACTIONS')):
        raise SystemExit('Performance measurements are local/manual only; do not run them in CI.')
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('fixture', 'image', 'output'):
        parser.add_argument('--' + option, type=Path, required=True)
    parser.add_argument('--initramfs', type=Path)
    parser.add_argument('--qemu', default='qemu-system-aarch64')
    parser.add_argument('--boots', type=int, default=3)
    args = parser.parse_args()
    if args.boots < 1:
        parser.error('--boots must be positive')
    args.fixture = args.fixture.resolve()
    args.image = args.image.resolve()
    args.initramfs = (args.initramfs or args.fixture / 'bootstrap.cpio').resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    metadata = {'qemu': subprocess.check_output([args.qemu, '--version'], text=True),
                'host': subprocess.check_output(['uname', '-a'], text=True),
                'sha256': {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                           for path in (args.image, args.initramfs, args.fixture / 'board.json')},
                'limits': ['Fresh guests, warm host disk cache, snapshot writes discarded',
                           'Guest-shell interval includes console attachment',
                           'Runtime metric ends at start submission, not first guest entry',
                           'Profile-enabled runs are diagnostic, not final performance results']}
    (args.output / 'environment.json').write_text(json.dumps(metadata, indent=2) + '\n')
    results = [boot(args, index) for index in range(args.boots)]
    (args.output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    keys = ('cold_start_to_vcpu_seconds', 'runtime_main_to_vcpu_ms', 'cold_start_to_guest_shell_seconds')
    summary = {key: {'median': statistics.median(r[key] for r in results),
                     'min': min(r[key] for r in results), 'max': max(r[key] for r in results)} for key in keys}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
