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
from shell_status import checked, status

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from board_bootstrap import services
from board_config import Board
from native_deployment import prepare as prepare_layout

CASES = ('missing-controller', 'provider-exit', 'closed-ready', 'invalid-ready',
         'timeout', 'missing-config', 'oversized-ready', 'launch-failure', 'critical-exit',
         'root-change', 'root-switch-failure', 'critical-exit-after-ready', 'missing-system-shell',
         'handoff-failure')
DEGRADED = b'HypeR init: VM fleet configuration unavailable; Native services remain available'
ADOPTED = b'HypeR init-system: core supervision adopted'
LIVE_PROVIDERS = frozenset(('closed-ready', 'invalid-ready', 'oversized-ready',
                            'timeout', 'missing-config', 'root-change'))
BOOT_FAILED = b'HypeR init: bootstrap failed'
CRITICAL = b'HypeR init: critical service '
KERNEL_FAILURES = (b'HypeR: fatal', b'kernel panic', b'HypeR crash monitor')


def prepare(args):
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    board = Board.parse(prepare_layout(
        json.loads((ROOT / 'boards/qemu.json').read_text()), 'ramfs'))
    board_path = args.output / 'board.json'
    board_path.write_text(json.dumps(board.source, indent=2) + '\n')
    clients = args.output / 'io-clients.conf'
    clients.write_text(board.bootstrap_clients())
    storage = args.output / 'storage.json'
    storage.write_text(json.dumps(board.storage_policy(), indent=2) + '\n')
    for mode in range(6):
        subprocess.run([str(args.sdk / 'bin/hyper-clang'), '-std=c17', '-Wall',
                        '-Wextra', '-Werror', '-static', f'-DREADINESS_TEST={mode}',
                        str(ROOT / 'tests/native/storage-readiness.c'),
                        '-o', str(args.output / f'provider-{mode}')], check=True)
    late_manager = args.output / 'late-critical-manager'
    subprocess.run([str(args.sdk / 'bin/hyper-clang'), '-std=c17', '-Wall',
                    '-Wextra', '-Werror', '-static', '-DVM_PROVISION_TEST=4',
                    str(ROOT / 'tests/native/vm-provisioning.c'),
                    '-o', str(late_manager)], check=True)
    empty_fleet = args.output / 'empty-fleet.json'
    empty_fleet.write_text(json.dumps({'format': 'hyper.vm-config', 'virtual-machines': []}) + '\n')
    invalid_image = args.output / 'not-elf'
    invalid_image.write_bytes(b'Invalid optional provider image\n')
    subprocess.run([str(args.sdk / 'bin/hyper-clang'), '-std=c17', '-Wall',
                    '-Wextra', '-Werror', '-static', str(ROOT / 'tests/native/root-switch.c'),
                    '-o', str(args.output / 'root-observer')], check=True)
    for case in (args.case,) if args.case else CASES:
        manifest = args.output / f'{case}.json'
        graph = services(board, ROOT)
        if case in ('critical-exit', 'critical-exit-after-ready'):
            next(item for item in graph['services']
                 if item['name'] == 'vm-manager')['critical'] = True
        if case in ('critical-exit-after-ready', 'missing-system-shell'):
            graph['virtual-machines']['config'] = '/boot/etc/hyper/vms.json'
        manifest.write_text(json.dumps(graph, indent=2) + '\n')
        provider = (args.boot_apps or args.apps) / 'io-runtime'
        replacements = []
        providers = ('provider-exit', 'closed-ready', 'invalid-ready', 'timeout',
                     'missing-config', 'oversized-ready')
        if case in providers:
            provider = args.output / f'provider-{providers.index(case)}'
        elif case == 'root-change':
            provider = args.output / 'root-observer'
        elif case in ('root-switch-failure', 'handoff-failure'):
            provider = args.output / 'provider-4'
        elif case == 'critical-exit':
            # Reach the committed system-init handoff without waiting for the
            # storage deadline; the critical system service then exits itself.
            provider = args.output / 'provider-4'
            replacements = ['--replace', f'svc/vm-manager={args.output / "provider-0"}']
        elif case in ('critical-exit-after-ready', 'missing-system-shell'):
            provider = args.output / 'provider-4'
            replacements = ['--replace', f'svc/vm-manager={late_manager}']
        composition = args.output / case
        subprocess.run([
            sys.executable, '-B', str(ROOT / 'scripts/pack-native-system.py'),
            '--packer', args.packer, '--strip', args.strip,
            '--output', str(composition),
            '--initramfs', str(args.output / f'{case}.cpio'),
            '--services', str(manifest),
            '--deployment', str(ROOT / 'mk/components.mk'), '--profile', 'system',
            '--apps', str(args.apps), '--boot-apps', str(args.boot_apps or args.apps),
            '--sdk', str(args.sdk), '--std', str(args.output),
            '--arch', 'aarch64', *replacements,
            '--boot-components', *args.boot_components, '--',
            '0755', 'svc/io-runtime', str(provider.resolve()),
            '0644', 'etc/hyper/board.json', str(board_path),
            '0644', 'etc/hyper/io-clients.conf', str(clients),
            '0644', 'etc/hyper/storage.json', str(storage),
            *(['0644', 'etc/hyper/vms.json', str(empty_fleet)]
              if case in ('critical-exit-after-ready', 'missing-system-shell') else []),
        ], check=True)
        if case == 'handoff-failure':
            subprocess.run([
                sys.executable, '-B', str(ROOT / 'tests/qemu/verify-boot-system.py'),
                'prepare-handoff', '--entries',
                str(composition / 'native-system/boot.entries.json'),
                '--output', str(args.output / f'{case}.cpio'),
                '--packer', args.packer, '--strip', args.strip,
            ], check=True)
        elif case == 'launch-failure':
            inject_invalid_provider(args, composition, invalid_image)
        elif case == 'missing-system-shell':
            entries = json.loads((composition / 'native-system/boot.entries.json').read_text())
            selected = [entry for entry in entries if entry[1] != 'system/bin/sh']
            if len(selected) != len(entries) - 1:
                raise AssertionError('expected exactly one formal shell image')
            incomplete = composition / 'missing-shell.entries.json'
            incomplete.write_text(json.dumps(selected, indent=2) + '\n')
            subprocess.run([
                sys.executable, '-B', str(ROOT / 'scripts/pack-native-initramfs.py'),
                '--packer', args.packer, '--strip', args.strip,
                '--output', str(args.output / f'{case}.cpio'),
                '--library-namespace', 'system', '--entries-from', str(incomplete),
            ], check=True)
        elif case == 'root-switch-failure':
            config = composition / 'native-system/system-init.json'
            policy = json.loads(config.read_text())
            policy['root-switch']['mounts'].append({
                'source': '/missing-root-volume', 'target': '/extra'})
            config.write_text(json.dumps(policy) + '\n')
            subprocess.run([
                sys.executable, '-B', str(ROOT / 'scripts/pack-native-initramfs.py'),
                '--packer', args.packer, '--strip', args.strip,
                '--output', str(args.output / f'{case}.cpio'),
                '--library-namespace', 'system',
                '--entries-from', str(composition / 'native-system/boot.entries.json'),
            ], check=True)


def inject_invalid_provider(args, composition, invalid_image):
    # Normal composition must enforce the static bootstrap ELF contract. Inject
    # the broken test image only after both real service graphs were validated.
    entries = json.loads((composition / 'native-system/boot.entries.json').read_text())
    selected = [entry for entry in entries if entry[1] == 'svc/io-runtime']
    if len(selected) != 1 or selected[0][0] != '0755':
        raise AssertionError('expected one executable bootstrap I/O provider')
    selected[0][2] = str(invalid_image)
    injected = composition / 'invalid-provider.entries.json'
    injected.write_text(json.dumps(entries, indent=2) + '\n')
    subprocess.run([
        sys.executable, '-B', str(ROOT / 'scripts/pack-native-initramfs.py'),
        '--packer', args.packer, '--strip', args.strip,
        '--output', str(args.output / 'launch-failure.cpio'),
        '--library-namespace', 'system', '--entries-from', str(injected),
    ], check=True)


def shell(session, command, *, allow_failure=False, recovery=False):
    if recovery:
        session.send(command + b"\necho STORAGE-''COMMAND-DONE\n")
        output = session.await_text(rb'STORAGE-COMMAND-DONE\n')
        session.await_text(rb'fallback-sh\$ ')
        failed = b'sh: ' in output
    else:
        session.send(checked(command))
        output = session.await_text(rb'hyper-sh\$ ')
        failed = status(output) != 0
    if not allow_failure and failed:
        raise AssertionError(f'Native command failed: {output!r}')
    return output


def run_case(args, case):
    logfile = args.output / f'{case}.log'
    # Deliberately no physical storage or network controller. The first case
    # uses the real io-runtime; the rest isolate readiness and launch failures.
    command = native_command(args.qemu, args.image, args.output / f'{case}.cpio')
    failures = KERNEL_FAILURES + (b'ROOT-SWITCH: FAIL',)
    if case not in ('critical-exit', 'critical-exit-after-ready', 'missing-system-shell'):
        failures += (BOOT_FAILED, CRITICAL)
    if case == 'handoff-failure':
        failures += (b'core supervision transferred', b'core supervision adopted')
    with Session(command, logfile, failures=failures) as session:
        if case == 'handoff-failure':
            session.await_text(rb'HypeR fallback shell: recovery console ready', 180)
            session.await_text(rb'fallback-sh\$ ')
            # A non-reading child must not steal subsequent pasted commands.
            session.send(b'/system/bin/echo RECOVERY_ONE\r/system/bin/echo RECOVERY_TWO\r')
            session.await_text(rb'(?m)^RECOVERY_ONE\n', 30)
            session.await_text(rb'(?m)^RECOVERY_TWO\n', 30)
            session.await_text(rb'fallback-sh\$ ')
            session.send(b'exit\r')
            session.await_text(rb'HypeR fallback shell: recovery console ready', 30)
            session.await_text(rb'fallback-sh\$ ')
            shell(session, b'/system/bin/echo RAW_RECOVERY_RESTARTED', recovery=True)
        elif case == 'root-switch-failure':
            session.await_text(re.escape(ADOPTED), 180)
            session.await_text(rb'root switch failed', 60)
            session.send(b'\n')
            session.await_text(rb'hyper-sh\$ ')
            for name in ('console-raw', 'session-manager', 'terminal-service', 'io-runtime', 'init-system'):
                processes = shell(session, f'ps --name {name}'.encode())
                if not re.search(name.encode() + rb'\s+running\b', processes):
                    raise AssertionError(f'root switch failure lost {name}: {processes!r}')
            processes = shell(session, b'ps --name vm-manager')
            if not re.search(rb'vm-manager\s+running\b', processes):
                raise AssertionError('formal graph did not retain its VM manager after root failure')
            unavailable = shell(session, b'vmm start alpine', allow_failure=True)
            if b'initial configuration is unavailable' not in unavailable:
                raise AssertionError('root failure unexpectedly admitted a VM fleet')
            shell(session, b'cat /etc/hyper/board.json')
            shell(session, b'ls -d /init')
            missing = shell(session, b'ls -d /.newroot', allow_failure=True)
            if status(missing) == 0:
                raise AssertionError('failed switch left its staging mount behind')
            if b'root namespace switched' in logfile.read_bytes():
                raise AssertionError('failed root preparation published a new root')
        elif case == 'critical-exit':
            session.await_text(re.escape(ADOPTED), 180)
            session.await_text(rb"HypeR init: critical service 'vm-manager' terminated: "
                               rb'reason=process-exited status=1')
            session.await_text(rb'HypeR init: critical service terminated')
            session.await_text(rb'hyper-sh\$ ', 60)
            # Formal init already owns supervision. The formal session manager
            # remains independent of the failed business service.
            for name in ('session-manager', 'terminal-service', 'console-raw', 'init-system'):
                processes = shell(session, f'ps --name {name}'.encode())
                if not re.search(name.encode() + rb'\s+running\b', processes):
                    raise AssertionError(f'boot-critical recovery lost {name}: {processes!r}')
            shell(session, b'echo FORMAL_CRITICAL_FAILURE_OK')
        elif case == 'critical-exit-after-ready':
            session.await_text(re.escape(ADOPTED), 180)
            session.await_text(rb'HypeR init: VM fleet configured', 60)
            session.await_text(rb'hyper-sh\$ ', 60)
            shell(session, b'echo FORMAL_STARTUP_COMMITTED')
            # The fixture waits for this explicit trigger, so failure cannot
            # race the startup decision merely because the host is slow.
            session.send(b'touch /tmp/critical-exit\n')
            session.await_text(rb"HypeR init: critical service 'vm-manager' terminated: "
                               rb'reason=process-exited status=1', 60)
            session.await_text(rb'HypeR init: critical service terminated', 60)
        elif case == 'missing-system-shell':
            session.await_text(re.escape(ADOPTED), 180)
            session.await_text(rb'HypeR init: VM fleet configured', 60)
            for _ in range(3):
                session.await_text(rb'HypeR session-manager: session ended:', 30)
        else:
            if case == 'timeout':
                session.await_text(rb'READINESS-FIXTURE-RUNNING')
                # No shell may consume terminal input before boot resolves.
                # The live provider is checked after the real readiness deadline.
                session.pump(1)
                if re.search(rb'(?:hyper|fallback)-sh\$ ', logfile.read_bytes()):
                    raise AssertionError('readiness wait started an early shell')
            # Exercise the production 120 s deadline, not a test-only override.
            session.await_text(re.escape(ADOPTED), 240)
            session.await_text(re.escape(DEGRADED), 120)
            session.await_text(rb'hyper-sh\$ ', 60)
            if case == 'root-change':
                # The observer can finish before init logs the unavailable
                # fleet. Check the complete capture, not only unread output.
                deadline = time.monotonic() + 60
                while b'ROOT-SWITCH: live capabilities preserved' not in logfile.read_bytes():
                    if time.monotonic() >= deadline:
                        raise AssertionError('live capability observer did not finish')
                    session.pump(0.1)
            shell(session, b'echo NATIVE_RECOVERY_OK')
            config = shell(session, b'cat /boot/etc/hyper/board.json')
            if b'hyper.board.v1' not in config:
                raise AssertionError('initramfs files are unavailable after storage failure')
            memory = shell(session, b'free --bytes')
            if not re.search(rb'Owners:[^\n]*guest=0 B', memory):
                raise AssertionError('failed bootstrap unexpectedly started a guest')
            processes = shell(session, b'ps')
            for name in (b'console-raw', b'session-manager', b'terminal-service',
                         b'init-system', b'vm-manager'):
                if not re.search(rb'\s' + name + rb'\s+running\b', processes):
                    raise AssertionError(f'base service {name!r} did not survive: {processes!r}')
            listing = shell(session, b'vmm list')
            if re.search(rb'^\S+\s+(?:starting|running|stopping|stopped|failed)\s+', listing, re.M):
                raise AssertionError('unavailable fleet published VM definitions')
            start = shell(session, b'vmm start alpine', allow_failure=True)
            if b'VM fleet unavailable: initial configuration is unavailable' not in start:
                raise AssertionError(f'vmm did not report unavailable configuration: {start!r}')
            deadline = time.monotonic() + (3 if case in LIVE_PROVIDERS else 30)
            while True:
                processes = shell(session, b'ps --name io-runtime')
                live = re.search(rb'io-runtime\s+running\b', processes)
                if case in LIVE_PROVIDERS:
                    # A readiness failure does not prove failed hardware
                    # ownership. Retain live services for recovery after handoff.
                    if not live:
                        raise AssertionError(f'{case}: recovery lost the live provider')
                    if time.monotonic() >= deadline:
                        break
                elif not re.search(rb'io-runtime\s+(?:running|stopping)\b', processes):
                    break
                elif time.monotonic() >= deadline:
                    raise AssertionError(f'{case}: failed provider did not terminate')
                session.pump(0.1)
    output = logfile.read_bytes()
    if case in ('critical-exit', 'critical-exit-after-ready') and DEGRADED in output:
        raise AssertionError('critical service failure was swallowed as optional storage failure')
    if case != 'handoff-failure' and b'HypeR fallback shell: recovery console ready' in output:
        raise AssertionError('a post-handoff failure reopened bootstrap recovery')
    evidence = {
        'provider-exit': b"'io-runtime' terminated: reason=process-exited status=1",
        'closed-ready': b'storage unavailable: readiness channel closed',
        'invalid-ready': b'storage unavailable: invalid readiness reply',
        'oversized-ready': b'storage unavailable: invalid readiness reply',
        'timeout': b'storage unavailable: readiness timed out',
        'launch-failure': b"failed to launch service 'io-runtime': service image or resources unavailable",
    }.get(case)
    if evidence is not None and evidence not in output:
        raise AssertionError(f'{case}: did not reach the intended failure boundary: {evidence!r}')
    if case == 'missing-config' and b'cannot open VM fleet configuration' not in output:
        raise AssertionError('missing-config scenario failed before opening the fleet file')
    # NOT_FOUND from the real controller claim must be fully queued before
    # READY closes. Init's emergency diagnostics may interleave with the line.
    if case == 'missing-controller' and b'Status(Status(-16))' not in output:
        raise AssertionError('provider failure diagnostic lost its controller error cause')
    expected_handoffs = 0 if case == 'handoff-failure' else 1
    if output.count(ADOPTED) != expected_handoffs:
        raise AssertionError(f'{case}: unexpected system-init handoff count')
    if case == 'handoff-failure' and b'HypeR session: console ready' in output:
        raise AssertionError('bootstrap recovery unexpectedly started the formal shell')
    if b'HypeR: vCPU 0 running as scheduler thread' in output:
        raise AssertionError('guest started after failed bootstrap')
    print(f'verified storage failure case: {case}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='action', required=True)
    prepare_parser = subparsers.add_parser('prepare')
    for name in ('sdk', 'apps'):
        prepare_parser.add_argument('--' + name, type=Path, required=True)
    prepare_parser.add_argument('--boot-apps', type=Path)
    prepare_parser.add_argument('--boot-components', nargs='+', required=True)
    for name in ('packer', 'strip'):
        prepare_parser.add_argument('--' + name, required=True)
    prepare_parser.add_argument('--output', type=Path, required=True)
    prepare_parser.add_argument('--case', choices=CASES)
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
