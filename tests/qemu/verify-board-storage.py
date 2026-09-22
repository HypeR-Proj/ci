#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise the Native FAT volume and verify its contents after a cold VM restart."""

import argparse
import importlib.util
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time


def boot(args, mode):
    launcher = Path(__file__).resolve().parents[2] / 'scripts/run-io-vm.py'
    command = [sys.executable, '-B', str(launcher), '--qemu', args.qemu,
               '--image', str(args.image), '--initramfs', str(args.initramfs),
               '--disk', str(args.disk), '--board', str(args.board)]
    with args.log.with_suffix(f'.{mode}.log').open('wb') as log, selectors.DefaultSelector() as selector:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT)
        pending = bytearray()
        selector.register(child.stdout, selectors.EVENT_READ)

        def await_text(pattern, timeout=180):
            limit = time.monotonic() + timeout
            while time.monotonic() < limit:
                match = re.search(pattern, pending)
                if match:
                    result = bytes(pending[:match.end()])
                    del pending[:match.end()]
                    return result
                if child.poll() is not None:
                    raise RuntimeError(f'QEMU exited with {child.returncode}')
                for key, _ in selector.select(0.1):
                    data = os.read(key.fd, 65536)
                    if not data:
                        raise RuntimeError('QEMU closed its console')
                    log.write(data)
                    log.flush()
                    pending.extend(data.replace(b'\r', b''))
                    if any(marker in pending for marker in (
                            b'Kernel panic', b'HypeR: fatal', b'HypeR KERNEL PANIC',
                            b'bootstrap failed', b'BOARD-STORAGE: ' + mode.encode() + b' FAIL',
                            b'HypeR io-runtime: failed:')):
                        # Preserve the complete crash report before terminating
                        # QEMU; its banner often arrives in a separate read.
                        drain_until = time.monotonic() + 3
                        while time.monotonic() < drain_until:
                            for diagnostic, _ in selector.select(0.1):
                                tail = os.read(diagnostic.fd, 65536)
                                if tail:
                                    log.write(tail)
                                    log.flush()
                                    pending.extend(tail.replace(b'\r', b''))
                        raise RuntimeError(f'boot/storage failure: {bytes(pending[-4096:])!r}')
            raise TimeoutError(f'waiting for {pattern!r}: {bytes(pending[-4096:])!r}')

        try:
            if args.require_userspace_device:
                await_text(rb'DEVICE-TEST: worker prepared\n')
            boot = await_text(rb'HypeR io-runtime: configuration volume: [0-9]+ sectors\n')
            if b'HypeR IO VM: ' not in boot:
                raise RuntimeError('forwarded I/O VM log prefix missing')
            child.stdin.write(f'/bin/storage-probe {mode}\n'.encode())
            child.stdin.flush()
            await_text(b'BOARD-STORAGE: ' + mode.encode() + b' PASS\n', timeout=600)
            await_text(rb'hyper-sh\$ ')
            child.stdin.write(b'cp /etc/hyper/vms.json /data/copied-vms.json\n')
            child.stdin.flush()
            copied = await_text(rb'hyper-sh\$ ')
            if b'sh: command failed' in copied or b'cp:' in copied:
                raise RuntimeError(f'copy across ramfs/FAT failed: {copied!r}')
            child.stdin.write(b'vmm status io\n')
            child.stdin.flush()
            observed = await_text(rb'hyper-sh\$ ')
            if not re.search(rb'\bio\s+running\s+yes\s+read-only', observed):
                raise RuntimeError(f'I/O VM observation unavailable: {observed!r}')
            if b'vCPUs:' not in observed or b'RAM capacity: 128 MiB' not in observed:
                raise RuntimeError(f'I/O VM metrics missing: {observed!r}')
            # The fixture admits 128 MiB RAM plus a 1 MiB initiator pool.
            resident = re.search(rb'allocated VM backing: ([0-9]+) bytes', observed)
            if resident is None or not 0 < int(resident[1]) <= 129 * 1024 * 1024:
                raise RuntimeError(f'I/O VM allocated backing missing or invalid: {observed!r}')
            child.stdin.write(b'vmm stop io\n')
            child.stdin.flush()
            refused = await_text(rb'hyper-sh\$ ')
            if b'I/O VM is read-only' not in refused:
                raise RuntimeError(f'I/O VM control was not rejected: {refused!r}')
            # Keep the real human-paced console path in coverage too.
            for character in b'echo BOARD-SHELL-RESPONSIVE\n':
                child.stdin.write(bytes([character]))
                child.stdin.flush()
                time.sleep(0.08)
            await_text(rb'\nBOARD-SHELL-RESPONSIVE\n')
        finally:
            child.stdin.close()
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            child.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--minimum-stack-remaining', type=int)
    parser.add_argument('--maximum-stack-used', type=int)
    parser.add_argument('--require-userspace-device', action='store_true',
                        help='require the generic userspace MMIO/IRQ worker')
    parser.add_argument('--verify-only', action='store_true',
                        help='inspect an existing acceptance disk without rewriting its payload')
    for name in ('image', 'initramfs', 'disk', 'board', 'log'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.minimum_stack_remaining is not None and args.minimum_stack_remaining < 0:
        parser.error('stack reserve must be nonnegative')
    if args.maximum_stack_used is not None and args.maximum_stack_used < 1:
        parser.error('maximum stack usage must be positive')
    args.log.parent.mkdir(parents=True, exist_ok=True)
    if not args.verify_only:
        boot(args, 'write')
    boot(args, 'verify')
    if args.minimum_stack_remaining is not None or args.maximum_stack_used is not None:
        spec = importlib.util.spec_from_file_location(
            'stack_acceptance', Path(__file__).with_name('verify-stack.py'))
        stack = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stack)
        for mode in (('verify',) if args.verify_only else ('write', 'verify')):
            stack.audit_summary(args.log.with_suffix(f'.{mode}.log').read_bytes(),
                                args.minimum_stack_remaining or 0,
                                required_kinds=frozenset({'kernel', 'user', 'irq'}),
                                maximum_used=args.maximum_stack_used)
    checks = ('existing Native volume readback and interactive shell' if args.verify_only
              else 'Native board storage, cold restart and interactive shell')
    print(f'{checks} passed: {args.log.parent}')


if __name__ == '__main__':
    main()
