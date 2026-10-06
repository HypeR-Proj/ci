#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Keep Native recovery tools usable when optional bootstrap storage fails."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from session import Session, native_command

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from board_bootstrap import services
from board_config import Board

CASES = ('missing-controller', 'provider-exit', 'closed-ready', 'invalid-ready',
         'timeout', 'missing-config', 'oversized-ready', 'launch-failure', 'critical-exit')
DEGRADED = b'HypeR init: VM fleet configuration unavailable; Native services remain available'
BOOT_FAILED = b'HypeR init: bootstrap failed'
CRITICAL = b'HypeR init: critical service '
KERNEL_FAILURES = (b'HypeR: fatal', b'kernel panic', b'HypeR crash monitor')


def prepare(args):
    args.output.mkdir(parents=True, exist_ok=True)
    board_path = ROOT / 'boards/qemu.json'
    board = Board.load(board_path)
    clients = args.output / 'io-clients.conf'
    clients.write_text(board.bootstrap_clients())
    for mode in range(6):
        subprocess.run([str(args.sdk / 'bin/hyper-clang'), '-std=c17', '-Wall',
                        '-Wextra', '-Werror', f'-DREADINESS_TEST={mode}',
                        str(ROOT / 'tests/native/storage-readiness.c'),
                        '-o', str(args.output / f'provider-{mode}')], check=True)
    invalid_image = args.output / 'not-elf'
    invalid_image.write_bytes(b'Invalid optional provider image\n')
    for case in CASES:
        manifest = args.output / f'{case}.json'
        manifest.write_text(json.dumps(services(board, ROOT), indent=2) + '\n')
        provider = args.apps / 'io-runtime'
        replacements = []
        providers = ('provider-exit', 'closed-ready', 'invalid-ready', 'timeout',
                     'missing-config', 'oversized-ready')
        if case in providers:
            mode = providers.index(case)
            provider = args.output / f'provider-{mode}'
        elif case == 'launch-failure':
            provider = invalid_image
        elif case == 'critical-exit':
            provider = args.output / 'provider-3'
            replacements = ['--replace', f'svc/vm-manager={args.output / "provider-0"}']
        subprocess.run([
            sys.executable, '-B', str(ROOT / 'scripts/pack-native-initramfs.py'),
            '--packer', args.packer, '--strip', args.strip,
            '--output', str(args.output / f'{case}.cpio'),
            '--deployment', str(ROOT / 'app/deployment.json'), '--profile', 'system',
            '--apps', str(args.apps), '--sdk', str(args.sdk), '--std', str(args.output),
            '--arch', 'aarch64', *replacements,
            '0755', 'svc/io-runtime', str(provider),
            '0644', 'etc/hyper/services.json', str(manifest),
            '0644', 'etc/hyper/board.json', str(board_path),
            '0644', 'etc/hyper/io-clients.conf', str(clients),
        ], check=True)


def shell(session, command, *, allow_failure=False):
    # A fence distinct from input echo also tolerates asynchronous service logs.
    session.send(command + b"\necho STORAGE-''COMMAND-DONE\n")
    output = session.await_text(rb'STORAGE-COMMAND-DONE\n')
    session.await_text(rb'hyper-sh\$ ')
    if not allow_failure and b'sh: command failed' in output:
        raise AssertionError(f'Native command failed: {output!r}')
    return output


def run_case(args, case):
    logfile = args.output / f'{case}.log'
    # Deliberately no physical storage or network controller. The first case
    # uses the real io-runtime; the rest isolate readiness and launch failures.
    command = native_command(args.qemu, args.image, args.output / f'{case}.cpio')
    failures = KERNEL_FAILURES
    if case != 'critical-exit':
        failures += (BOOT_FAILED, CRITICAL)
    with Session(command, logfile, failures=failures) as session:
        if case == 'critical-exit':
            session.await_text(rb'(?s)(?=.*' + re.escape(CRITICAL)
                               + rb')(?=.*' + re.escape(BOOT_FAILED) + rb')')
        else:
            if case == 'timeout':
                session.await_text(rb'READINESS-FIXTURE-RUNNING')
                processes = shell(session, b'ps --name io-runtime')
                if not re.search(rb'io-runtime\s+running\b', processes):
                    raise AssertionError('timeout fixture did not remain alive during READY wait')
            # Exercise the production 120 s deadline, not a test-only override.
            session.await_text(re.escape(DEGRADED), 240)
            shell(session, b'echo NATIVE_RECOVERY_OK')
            config = shell(session, b'cat /etc/hyper/board.json')
            if b'hyper.board.v1' not in config:
                raise AssertionError('initramfs files are unavailable after storage failure')
            memory = shell(session, b'free --bytes')
            if not re.search(rb'Owners:[^\n]*guest=0 B', memory):
                raise AssertionError('failed bootstrap unexpectedly started a guest')
            processes = shell(session, b'ps')
            for name in (b'console-input', b'console-output', b'session', b'vm-manager'):
                if not re.search(rb'\s' + name + rb'\s+running\b', processes):
                    raise AssertionError(f'base service {name!r} did not survive: {processes!r}')
            listing = shell(session, b'vmm list')
            if re.search(rb'^\S+\s+(?:starting|running|stopping|stopped|failed)\s+', listing, re.M):
                raise AssertionError('unavailable fleet published VM definitions')
            start = shell(session, b'vmm start alpine', allow_failure=True)
            if b'VM fleet unavailable: initial configuration is unavailable' not in start:
                raise AssertionError(f'vmm did not report unavailable configuration: {start!r}')
            deadline = time.monotonic() + 30
            while True:
                processes = shell(session, b'ps --name io-runtime')
                if case == 'missing-config':
                    # Failure to read the fleet file must not stop a provider
                    # that already established storage readiness.
                    if not re.search(rb'io-runtime\s+running\b', processes):
                        raise AssertionError('configuration failure stopped a ready provider')
                    break
                if not re.search(rb'io-runtime\s+(?:running|stopping)\b', processes):
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError('abandoned provider was not stopped')
                session.pump(0.1)
    output = logfile.read_bytes()
    if case == 'critical-exit' and DEGRADED in output:
        raise AssertionError('critical service failure was swallowed as optional storage failure')
    if case == 'timeout' and b'storage unavailable: readiness timed out' not in output:
        raise AssertionError('timeout scenario failed before the actual READY deadline')
    if case == 'missing-config' and b'cannot open VM fleet configuration' not in output:
        raise AssertionError('missing-config scenario failed before opening the fleet file')
    # NOT_FOUND from the real controller claim must be fully queued before
    # READY closes. Init's emergency diagnostics may interleave with the line.
    if case == 'missing-controller' and b'Status(Status(-16))' not in output:
        raise AssertionError('provider failure diagnostic lost its controller error cause')
    if b'HypeR: vCPU 0 running as scheduler thread' in output:
        raise AssertionError('guest started after failed bootstrap')
    print(f'verified storage failure case: {case}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='action', required=True)
    prepare_parser = subparsers.add_parser('prepare')
    for name in ('sdk', 'apps'):
        prepare_parser.add_argument('--' + name, type=Path, required=True)
    for name in ('packer', 'strip'):
        prepare_parser.add_argument('--' + name, required=True)
    prepare_parser.add_argument('--output', type=Path, required=True)
    run_parser = subparsers.add_parser('run')
    run_parser.add_argument('--qemu', required=True)
    run_parser.add_argument('--image', type=Path, required=True)
    run_parser.add_argument('--output', type=Path, required=True)
    run_parser.add_argument('--case', choices=CASES)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args)
    else:
        for case in (args.case,) if args.case else CASES:
            run_case(args, case)


if __name__ == '__main__':
    main()
