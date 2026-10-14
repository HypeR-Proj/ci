#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise disk-backed Native programs and the disk-independent recovery shell."""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from session import Session, native_command
from shell_status import checked, status

PROVIDER_REJECTIONS = {'missing-io-runtime': 'io-runtime', 'missing-fs-erofs': 'fs-erofs'}


def prepare_provider_rejection(argv):
    """Remove one successor executable from a copied system tree and new disk."""
    parser = argparse.ArgumentParser(description=prepare_provider_rejection.__doc__)
    parser.add_argument('--case', choices=PROVIDER_REJECTIONS, required=True)
    for name in ('system-root', 'board', 'output', 'mkfs-erofs'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--artifact', action='append', default=[], metavar='NAME=PATH')
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    system = args.output / 'system-root'
    shutil.copytree(args.system_root, system, symlinks=True)
    (system / 'svc' / PROVIDER_REJECTIONS[args.case]).unlink()
    pack = [sys.executable, '-B', str(Path(__file__).resolve().parents[2]
                                    / 'scripts/pack-board-image.py'),
            '--board', str(args.board), '--output', str(args.output / 'disk.img'),
            '--system-root', str(system), '--mkfs-erofs', str(args.mkfs_erofs)]
    for artifact in args.artifact:
        pack.extend(['--artifact', artifact])
    subprocess.run(pack, check=True)


def prepare_handoff(argv):
    """Copy boot policy and inject an exiting candidate before ownership commit."""
    parser = argparse.ArgumentParser(description=prepare_handoff.__doc__)
    parser.add_argument('--entries', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--packer', required=True)
    parser.add_argument('--strip', required=True)
    args = parser.parse_args(argv)
    entries = json.loads(args.entries.read_text())
    candidates = [record for record in entries if record[1] == 'etc/hyper/init.json']
    if len(candidates) != 1:
        parser.error('boot entry manifest must contain exactly one init policy')
    record = candidates[0]
    policy = json.loads(Path(record[2]).read_text())
    policy['system-init'] = '/bin/echo'
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    policy_path = output.parent / 'handoff-init.json'
    policy_path.write_text(json.dumps(policy, indent=2) + '\n')
    record[2] = str(policy_path)
    entries_path = output.parent / 'handoff.entries.json'
    entries_path.write_text(json.dumps(entries, indent=2) + '\n')
    subprocess.run([
        sys.executable, '-B', str(Path(__file__).resolve().parents[2]
                                / 'scripts/pack-native-initramfs.py'),
        '--packer', args.packer, '--strip', args.strip, '--output', str(output),
        '--library-namespace', 'system', '--entries-from', str(entries_path),
    ], check=True)


def command(session, text, expected=None, *, fails=False, timeout=180, recovery=False):
    session.send(text.encode() + b'\n' if recovery or text == 'exit' else checked(text))
    output = session.await_text(rb'(?:hyper|fallback)-sh\$ ', timeout=timeout)
    if recovery:
        failed = any(marker in output for marker in (
            b'sh: command failed', b'sh: command launch failed', b'sh: Native operation failed'))
    else:
        failed = text != 'exit' and status(output) != 0
    if failed != fails:
        raise AssertionError(f'{text}: expected failure={fails}: {output!r}')
    if expected is not None and not re.search(expected, output):
        raise AssertionError(f'{text}: missing {expected!r}: {output!r}')
    return output


def memory_accounting(run):
    """Check arithmetic and disjoint ownership from the real Native inspector."""
    output = run('free --bytes', rb'Mem:')
    mem = re.search(rb'^Mem:([^\n]+)', output, re.MULTILINE)
    totals = re.search(rb'^Reserved: (\d+) B  Managed: (\d+) B  Allocated: (\d+) B',
                       output, re.MULTILINE)
    owners = re.search(rb'^Owners:([^\n]+)', output, re.MULTILINE)
    if not mem or not totals or not owners:
        raise AssertionError(f'incomplete memory report: {output!r}')
    values = [None if value == '—' else int(value[:-2])
              for value in re.findall(r'\d+ B|—', mem[1].decode())]
    if len(values) != 5:
        raise AssertionError(f'invalid main memory row: {output!r}')
    total, used, free, reclaimable, available = values
    reserved, managed, allocated = map(int, totals.groups())
    ownership = {name: None if value == '—' else int(value[:-2]) for name, value in
                 re.findall(r'([a-z/-]+)=(\d+ B|—)', owners[1].decode())}
    if set(ownership) != {'kernel', 'user', 'guest', 'buffered/cached', 'other'}:
        raise AssertionError(f'incomplete owner partition: {output!r}')
    if total != reserved + managed or managed != free + allocated:
        raise AssertionError(f'physical memory does not balance: {output!r}')
    if any(ownership[key] is None for key in ('user', 'guest', 'other')):
        raise AssertionError(f'missing physical owner counters: {output!r}')
    if (ownership['kernel'] is None) != (ownership['buffered/cached'] is None):
        raise AssertionError(f'inconsistent slab census validity: {output!r}')
    if ownership['kernel'] is not None and sum(ownership.values()) != allocated:
        raise AssertionError(f'owners double count or omit backing: {output!r}')
    if ownership['kernel'] is None and reclaimable is not None:
        raise AssertionError(f'unknown slab census presented as reclaimable: {output!r}')
    if reclaimable is None:
        if used is not None or available is not None:
            raise AssertionError(f'partial census presented as complete: {output!r}')
    elif available != free + reclaimable or total != used + free + reclaimable:
        raise AssertionError(f'derived memory does not balance: {output!r}')
    return ownership


def retired_bootstrap(session, run, logfile):
    """Require resource takeover and archive detachment before ordinary work."""
    marker = b'HypeR init: bootstrap retired; boot archive detached'
    deadline = time.monotonic() + 120
    while marker not in logfile.read_bytes():
        if time.monotonic() >= deadline:
            raise AssertionError('formal init did not retire bootstrap resources')
        session.pump(0.1)
    run('ls /boot/init', fails=True)
    listing = run('ls -1 /boot')
    if re.search(rb'^[a-zA-Z0-9_.-]+[/@]?\r?$', listing, re.MULTILINE):
        raise AssertionError(f'bootstrap archive remains reachable: {listing!r}')
    processes = run('ps --no-headers')
    running = re.findall(rb'^process\s+0x[0-9a-f]+\s+-\s+(\S+)\s+running\b',
                         processes, re.MULTILINE)
    for name in (b'fallback-sh',):
        if name in running:
            raise AssertionError(f'bootstrap process remains live: {name!r}')
    for name in (b'console-raw', b'io-runtime', b'fs-backend', b'init'):
        if running.count(name) != 1:
            raise AssertionError(f'expected one adopted {name!r}: {processes!r}')
    initial = re.search(rb'memory \(kernel initialized\): \d+ MiB RAM, (\d+) reserved pages',
                        logfile.read_bytes())
    # Namespace detachment can precede the final retired process's image
    # release. Require actual reclamation, allowing that cleanup to finish.
    deadline = time.monotonic() + 30
    while True:
        report = run('free --bytes', rb'Reserved:')
        reserved = re.search(rb'^Reserved: (\d+) B', report, re.MULTILINE)
        if not initial or not reserved:
            raise AssertionError(f'missing archive reservation evidence: {report!r}')
        if int(reserved[1]) < int(initial[1]) * 4096:
            break
        if time.monotonic() >= deadline:
            raise AssertionError(f'initial archive reservation did not shrink: {report!r}')
        session.pump(0.1)


def check_root_layout(listing):
    names = set(re.findall(rb'^([a-z][a-z0-9-]*[/@]?)\r?$', listing, re.MULTILINE))
    required = {b'bin/', b'etc/', b'lib@', b'lib64/', b'svc/', b'boot/', b'data/', b'tmp/'}
    if not required <= names or b'system/' in names:
        raise AssertionError(f'unexpected formal root layout: {listing!r}')


def require_order(evidence, markers):
    missing = [marker for marker in markers if marker not in evidence]
    if missing:
        raise AssertionError(f'missing handoff evidence: {missing!r}')
    positions = [evidence.index(marker) for marker in markers]
    if positions != sorted(positions):
        raise AssertionError('filesystem mounts crossed the init handoff boundary')


def check_handoff_order(evidence):
    require_order(evidence, (
        b'EROFS mounted at /system',
        b'HypeR bootstrap-stage1-init: provisional system root active; preparing successor',
        b'HypeR init: core supervision adopted',
        b'FAT mounted at /data', b'HypeR init: VM fleet configured',
        b'HypeR init: bootstrap retired; boot archive detached',
    ))


def check_rollback_order(evidence):
    require_order(evidence, (
        b'HypeR bootstrap-stage1-init: provisional system root active; preparing successor',
        b'HypeR bootstrap-stage1-init: system handoff rejected; bootstrap root restored',
        b'HypeR bootstrap-stage1-fallback-sh: recovery console ready',
    ))
    if b'core supervision transferred' in evidence or b'core supervision adopted' in evidence:
        raise AssertionError('candidate failure crossed the ownership commit boundary')


def check_provider_rejection(case, evidence):
    """A rejected real replacement must leave the original mount serving."""
    require_order(evidence, (
        b'HypeR init: core supervision adopted',
        b'HypeR init: provider adoption failed:',
        b'HypeR session: console ready',
    ))
    if b'bootstrap retirement disabled' not in evidence:
        raise AssertionError('failed provider preparation did not disable retirement')
    for forbidden in (b'bootstrap retired; boot archive detached',
                      b'bootstrap-stage1-fallback-sh: recovery console ready',
                      b'HypeR fs-backend: filesystem ownership adopted'):
        if forbidden in evidence:
            raise AssertionError(f'rejected provider crossed its boundary: {forbidden!r}')
    if b'bootstrap-stage1-fs-backend: adoption preparation failed; bootstrap mounts retained:' not in evidence:
        raise AssertionError('original filesystem manager did not resume administration')
    if case == 'missing-fs-erofs':
        if b'EROFS recognized for /, but filesystem worker is unavailable' not in evidence:
            raise AssertionError('replacement failed outside worker preparation')
        if b'HypeR io-runtime: running backend adopted' not in evidence:
            raise AssertionError('filesystem rejection fixture never adopted the I/O owner')
    elif case == 'missing-io-runtime':
        if b'replacement preparation failed; bootstrap provider retained' not in evidence:
            raise AssertionError('I/O candidate unexpectedly passed construction')
        if b'HypeR io-runtime: running backend adopted' in evidence:
            raise AssertionError('missing I/O candidate somehow adopted the live VM')
    else:
        raise AssertionError(f'unknown provider rejection: {case}')


def retained_provider_io(run):
    """Exercise old live providers after formal init abandoned preparation."""
    run('ls /boot/init', rb'\binit\b')
    for name in ('init', 'io-runtime', 'fs-backend', 'fs-erofs'):
        run(f'ps --name {name}', name.encode() + rb'\s+running')
    run('cat /etc/hyper/init.json', rb'services')
    mounts = run('mount', rb' on / type erofs')
    if b' on /system type ' in mounts:
        raise AssertionError('retained manager reported the provisional root path')
    # Automatic mounts stop after failed adoption. Explicitly exercise the
    # predecessor's administration endpoint, write path and returned block owner.
    run('mount /data')
    run('echo PROVIDER-REJECTION-IO > /data/provider-rejection.txt')
    run('cat /data/provider-rejection.txt', rb'\nPROVIDER-REJECTION-IO\n')
    run('umount /data')
    run('mount /data')
    run('cat /data/provider-rejection.txt', rb'\nPROVIDER-REJECTION-IO\n')
    run('rm /data/provider-rejection.txt')
    run('umount /data')


def check_manager_evidence(case, output):
    """Require the intended failure boundary, not an unrelated fixture exit."""
    markers = {
        'absent': (),
        'missing': (b"failed to launch service 'vm-manager': service image or resources unavailable",),
        'exit': (b'VM-PROVISION-FIXTURE-EXIT', b"'vm-manager' terminated: reason=process-exited status=1"),
        'closed-reply': (b'VM-PROVISION-FIXTURE-CLOSED-REPLY',
                         b'VM fleet result channel closed without a reply',
                         b"'vm-manager' terminated: reason=requested"),
        'invalid-reply': (b'VM-PROVISION-FIXTURE-INVALID-REPLY',
                          b'VM fleet configuration reply is invalid',
                          b"'vm-manager' terminated: reason=requested"),
        'configured-exit': (b'HypeR init: VM fleet configured',
                            b'VM-PROVISION-FIXTURE-CONFIGURED',
                            b"'vm-manager' terminated: reason=process-exited status=0"),
        'no-config': (b'HypeR vm-manager: initial fleet configuration unavailable',),
    }
    for marker in markers[case]:
        if marker not in output:
            raise AssertionError(f'manager {case}: missing {marker!r}')


def nested_shell(session, parent_prompt, *, storage, virtual_machines):
    """Exercise nested formal shells with only system-side capabilities."""
    for image in ('/bin/sh', 'sh'):
        session.send(image.encode() + b' -i\n')
        session.await_text(rb'HypeR session: console ready', 180)
        session.await_text(rb'hyper-sh\$ ', 30)
    command(session, 'ps', rb'NAME')
    command(session, 'handle --summary --kind process', rb'\bprocess\s+[1-9]')
    command(session, 'free --bytes', rb'Mem:')
    # An identical tool at an ordinary path receives no diagnostic authority.
    if not storage:
        command(session, 'mkdir -p /tmp')
    untrusted = '/data/untrusted-handle' if storage else '/tmp/untrusted-handle'
    command(session, f'cp /bin/handle {untrusted}')
    command(session, f'{untrusted} --summary',
            rb'object-inspector capability unavailable', fails=True)
    command(session, f'rm {untrusted}')
    if storage:
        # Nested shells use formal mount tools without retaining bootstrap
        # image or loader capabilities.
        command(session, 'mount', rb' on / type erofs')
    if virtual_machines:
        command(session, 'vmm list', rb'alpine\s+stopped')
    command(session, 'echo NESTED-SHELL-OK', rb'\nNESTED-SHELL-OK\n')
    session.send(b'exit\n')
    session.await_text(rb'hyper-sh\$ ', 30)
    session.send(b'exit\n')
    session.await_text(parent_prompt, 30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    for name in ('image', 'initramfs', 'log'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--disk', type=Path)
    parser.add_argument('--board', type=Path)
    parser.add_argument('--recovery', action='store_true',
                        help='omit controllers and require the bootstrap shell to survive')
    parser.add_argument('--failed-handoff', action='store_true',
                        help='supplied archive selects an exiting system-init candidate (for example /bin/echo)')
    parser.add_argument('--missing-shell', action='store_true',
                        help='supplied system payload omits bin/sh; require retries without bootstrap fallback')
    parser.add_argument('--missing-worker', action='store_true',
                        help='boot a supplied archive with fs-erofs removed; keep controllers present')
    parser.add_argument('--missing-data-worker', action='store_true',
                        help='boot and system payloads omit fs-fat; require formal init and Native services to survive')
    parser.add_argument('--provider-case', choices=PROVIDER_REJECTIONS,
                        help='reject the real replacement before resource transfer; retain bootstrap providers')
    parser.add_argument('--manager-case', choices=(
        'absent', 'missing', 'exit', 'closed-reply', 'invalid-reply', 'configured-exit', 'no-config'),
        help='verify storage with the corresponding supplied system service graph or fault fixture')
    args = parser.parse_args()
    if sum((args.recovery, args.missing_worker, args.missing_data_worker,
            args.failed_handoff, args.missing_shell, bool(args.manager_case),
            bool(args.provider_case))) > 1:
        parser.error('select only one failure scenario')
    if args.recovery:
        launch = native_command(args.qemu, args.image, args.initramfs)
    else:
        if not args.disk or not args.board:
            parser.error('normal boot requires --disk and --board')
        launch = [sys.executable, '-B', str(Path(__file__).resolve().parents[2]
                  / 'scripts/run-io-vm.py'), '--qemu', args.qemu,
                  '--image', str(args.image), '--initramfs', str(args.initramfs),
                  '--disk', str(args.disk), '--board', str(args.board)]
    args.log.parent.mkdir(parents=True, exist_ok=True)
    failures = (b'HypeR: fatal', b'HypeR KERNEL PANIC', b'Kernel panic',
                b'HypeR init: bootstrap failed',
                b'HypeR bootstrap-stage1-init: bootstrap failed')
    if not any((args.recovery, args.missing_worker, args.missing_data_worker,
                args.failed_handoff, args.missing_shell, args.manager_case, args.provider_case)):
        failures += (b'provider adoption failed', b'I/O adoption unconfirmed',
                     b'bootstrap retirement deferred')
    if args.failed_handoff:
        # These are irreversible ownership markers: neither may appear during
        # recovery, even after the candidate exits and I/O is exercised.
        failures += (b'core supervision transferred', b'core supervision adopted')
    with Session(launch, args.log, failures=failures) as session:
        if args.missing_worker:
            session.await_text(rb'EROFS recognized for /system, but filesystem worker is unavailable', 240)
        if args.recovery or args.missing_worker or args.failed_handoff:
            session.await_text(rb'system init unavailable; retaining core supervision and recovery shell', 180)
        else:
            session.await_text(rb'HypeR init: core supervision adopted', 240)
            if args.manager_case in ('exit', 'closed-reply', 'invalid-reply', 'configured-exit'):
                session.await_text(rb"HypeR init: service 'vm-manager' terminated", 180)
            elif args.provider_case or args.missing_data_worker or args.manager_case in ('missing', 'no-config'):
                session.await_text(rb'VM fleet configuration unavailable', 180)
            elif args.manager_case == 'absent':
                pass
            elif args.recovery:
                session.await_text(rb'VM fleet configuration unavailable', 180)
            else:
                session.await_text(rb'HypeR init: VM fleet configured', 240)
        # Only failures before supervision transfer belong to bootstrap.
        # Formal mount/service failures keep the formal console.
        recovery = (args.recovery or args.missing_worker
                    or args.failed_handoff)
        if args.missing_shell:
            for _ in range(3):
                session.await_text(rb'HypeR session-manager: session ended:', 30)
            if b'HypeR bootstrap-stage1-fallback-sh:' in args.log.read_bytes():
                raise AssertionError('missing formal shell reopened bootstrap recovery')
            print(f'Boot/system missing shell passed: {args.log}')
            return
        prompt = rb'fallback-sh\$ ' if recovery else rb'hyper-sh\$ '
        session.await_text(prompt, 180)

        def run(text, expected=None, **kwargs):
            return command(session, text, expected, recovery=recovery, **kwargs)

        if args.provider_case:
            retained_provider_io(run)
            check_provider_rejection(args.provider_case, args.log.read_bytes())
            print(f'Boot/system provider {args.provider_case} passed: {args.log}')
            return

        normal = not (recovery or args.recovery or args.missing_data_worker or args.manager_case)
        if normal:
            retired_bootstrap(session, run, args.log)
        run('pwd', rb'\n/\n')
        if recovery:
            run('help', rb'Recovery shell:')
        else:
            run('pwd | sh -c pwd', rb'\n/\n')
        if not recovery:
            nested_shell(session, prompt,
                         storage=not args.missing_data_worker,
                         virtual_machines=not (recovery or args.recovery or args.missing_data_worker or args.manager_case))
        switched = not (args.recovery or args.missing_worker or args.failed_handoff)
        if switched:
            check_root_layout(run('ls -1 /'))
            run('exit')
            run('pwd | sh -c pwd', rb'\n/\n')
        if args.missing_data_worker:
            for name in ('init', 'io-runtime', 'fs-backend', 'vm-manager'):
                run(f'ps --name {name}', name.encode() + rb'\s+running')
            mounts = run('mount', rb' on / type erofs')
            if b' on /data type ' in mounts:
                raise AssertionError('missing data worker unexpectedly mounted /data')
            run('mount /data', rb'FAT recognized for /data, but filesystem worker is unavailable',
                    fails=True)
            run('vmm start alpine', rb'initial configuration is unavailable', fails=True)
        if args.failed_handoff:
            # The candidate ran against the real provisional root. Its exit
            # must restore the same bootstrap mounts before recovery starts.
            evidence = args.log.read_bytes()
            check_rollback_order(evidence)
            mounts = run('mount', rb'PARTUUID=[0-9a-f-]+ on /system type erofs \(ro\)')
            if b' on /data type ' in mounts:
                raise AssertionError('data mounted before formal init took ownership')
            # The fallback has only static bootstrap clients. Exercise the old
            # manager after its migration endpoint closed and topology rolled back.
            run('mount -T /system/etc/fstab /data')
            run('mount', rb'PARTUUID=[0-9a-f-]+ on /data type fat')
            run('umount /data')
            run('mount -T /system/etc/fstab /data')
            run('umount /data')
            session.pump(2)
        if args.manager_case:
            # Real Native storage and core service processes must survive all
            # business-manager failure boundaries, before and after handoff.
            for name in ('io-runtime', 'fs-backend', 'init'):
                run(f'ps --name {name}', name.encode() + rb'\s+running')
            processes = run('ps --name vm-manager')
            if args.manager_case == 'no-config':
                if not re.search(rb'vm-manager\s+running', processes):
                    raise AssertionError('manager without initial fleet config did not survive')
                run('vmm start alpine', rb'initial configuration is unavailable', fails=True)
            else:
                if re.search(rb'vm-manager\s+running', processes):
                    raise AssertionError('unexpected live business VM manager')
                # The usual command timeout is 30 s. Closure must beat it, so a
                # reserved but unserved endpoint cannot masquerade as recovery.
                run('vmm list', rb'Status\(Status\(-15\)\)', fails=True, timeout=10)
            run('cp /data/vms.json /data/manager-isolation.txt')
            run('cat /data/manager-isolation.txt', rb'virtual-machines')
            run('umount /data')
            run('mount /data')
            run('cat /data/manager-isolation.txt', rb'virtual-machines')
            run('rm /data/manager-isolation.txt')
        if not (recovery or args.recovery or args.missing_data_worker or args.manager_case):
            system_services = run('ls /svc', rb'\binit\b')
            for provider in (b'fs-fat', b'fs-erofs', b'fs-backend', b'io-runtime', b'console-raw'):
                if not re.search(rb'\b' + provider + rb'\b', system_services):
                    raise AssertionError(f'missing formal provider {provider!r}')
            run('ls /bin', rb'(?i)\bhandle\b')
            run('ldd --loader /lib64/ld-hyper-aarch64.so '
                    '--library-dir /lib64/aarch64-hyper-hyper /bin/ls',
                    rb'libhyper.* => /lib64/')
            run('ps --name vm-manager', rb'vm-manager\s+running')
            run('handle --summary --kind process', rb'\bprocess\s+[1-9]')
            baseline_memory = memory_accounting(run)
            if baseline_memory['guest'] == 0:
                raise AssertionError('live I/O VM backing was not accounted as guest memory')
            run('echo SPLIT-SYSTEM-IO > /data/boot-system-test.txt')
            run('cat /data/boot-system-test.txt', rb'\nSPLIT-SYSTEM-IO\n')
            run('mkdir /data/boot-system-dir')
            run('cp /data/boot-system-test.txt /data/boot-system-dir/original')
            run('mv /data/boot-system-dir/original /data/boot-system-dir/renamed')
            run('ls -1 /data/boot-system-dir', rb'\nrenamed\n')
            run('cat /data/boot-system-dir/renamed', rb'\nSPLIT-SYSTEM-IO\n')
            run('rm /data/boot-system-dir/renamed')
            run('rmdir /data/boot-system-dir')
            run('mount', rb'PARTUUID=[0-9a-f-]+ on / type erofs \(ro\)')
            run('touch /must-not-write', fails=True)
            run('umount /', fails=True)
            # VM definitions retain their image file even while stopped.
            run('umount /data', fails=True)
            run('vmm list', rb'alpine\s+stopped')
            run('vmm start alpine --wait --timeout 60', rb'alpine: running')
            running_memory = memory_accounting(run)
            if running_memory['guest'] <= baseline_memory['guest']:
                raise AssertionError('Alpine backing was not added to guest memory')
            run('vmm stop alpine --wait --timeout 60', rb'alpine: stopped')
            # Lifecycle completion precedes asynchronous backend/VMO retirement.
            deadline = time.monotonic() + 60
            while memory_accounting(run)['guest'] != baseline_memory['guest']:
                if time.monotonic() >= deadline:
                    raise AssertionError('guest backing accounting did not retire')
                session.pump(0.1)
            # Reopen the image and allocate a fresh guest after the first
            # adopted runtime has retired all backend resources.
            run('vmm start alpine --wait --timeout 60', rb'alpine: running')
            run('vmm stop alpine --wait --timeout 60', rb'alpine: stopped')
            run('vmm delete alpine')
            run('umount /data')
            run('mount -o ro PARTLABEL=hyper-data /data')
            run('cat /data/boot-system-test.txt', rb'\nSPLIT-SYSTEM-IO\n')
            run('touch /data/must-not-write', fails=True)
            run('umount /data')
            run('mount /data')
            run('rm /data/boot-system-test.txt')
    if not recovery and b'HypeR bootstrap-stage1-fallback-sh: recovery console ready' in args.log.read_bytes():
        raise AssertionError('successful boot started the fallback shell')
    if not (recovery or args.recovery or args.missing_data_worker or args.manager_case):
        evidence = args.log.read_bytes()
        check_handoff_order(evidence)
    if args.manager_case:
        check_manager_evidence(args.manager_case, args.log.read_bytes())
    if args.missing_data_worker:
        evidence = args.log.read_bytes()
        if b'HypeR init: formal mounts unavailable:' not in evidence:
            raise AssertionError('missing FAT worker was not reported by formal init')
        if evidence.index(b'core supervision adopted') > evidence.index(b'FAT recognized for /data'):
            raise AssertionError('data failure occurred before formal handoff')
    mode = (f'manager {args.manager_case}' if args.manager_case else
            'handoff rollback' if args.failed_handoff else
            'recovery' if args.recovery or args.missing_worker else 'deployment')
    print(f'Boot/system {mode} passed: {args.log}')


if __name__ == '__main__':
    if sys.argv[1:2] == ['prepare-handoff']:
        prepare_handoff(sys.argv[2:])
    elif sys.argv[1:2] == ['prepare-provider-rejection']:
        prepare_provider_rejection(sys.argv[2:])
    else:
        main()
