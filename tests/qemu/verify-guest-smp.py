#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise Linux SMP, host-CPU migration, hotplug, reboot, and poweroff."""

import os
from functools import partial
from pathlib import Path
import re
import sys
import time

from session import Session, native_command

from guest_console import append_console_output


# Shared CI TCG hosts can exceed 90 seconds while a fresh Alpine SMP guest
# is still progressing (observed IPv6 initialization at guest t=76s). This
# remains a fixed total boot deadline; ordinary commands keep 90 seconds.
GUEST_BOOT_TIMEOUT_SECONDS = 180


def main():
    qemu, image, initramfs, logfile = sys.argv[1:]
    guest_cpus = int(os.environ.get('GUEST_CPUS', '4'))
    host_cpus = int(os.environ.get('QEMU_CPUS', '4'))
    if not 2 <= guest_cpus <= 8:
        raise ValueError('SMP acceptance requires 2..8 guest CPUs')
    command = native_command(qemu, image, initramfs)
    Path(logfile).parent.mkdir(parents=True, exist_ok=True)
    with Session(command, logfile, cleanup_timeout=5,
                 output_filter=partial(append_console_output, filter_guest_logs=True)) as session:
        pending = session.pending

        def await_text(pattern, timeout=90):
            return session.await_text(pattern, timeout, match_only=True)

        def send(command):
            session.send(command + b'\n')

        def attach():
            # A reboot creates a fresh runtime asynchronously. Status polling
            # here is a test deadline, not the runtime notification mechanism.
            for _ in range(90):
                send(b'vmm status alpine')
                status = await_text(rb'alpine\s+(?:starting|running|stopping|stopped|failed)')
                await_text(rb'hyper-sh\$ ')
                if status.endswith(b'failed'):
                    raise RuntimeError('VM failed during guest reset')
                if status.endswith(b'running'):
                    send(b'vmm console alpine')
                    result = await_text(rb'Connected to alpine\.|hyper-sh\$ ')
                    if result.startswith(b'Connected'):
                        # Reattachment need not replay an already consumed
                        # shell prompt; request a fresh empty command response.
                        send(b'')
                        await_text(rb'~ # ', timeout=GUEST_BOOT_TIMEOUT_SECONDS)
                        return
                    # The old runtime may exit between status and attach.
                    # A failed attach returns to the host shell; retry until
                    # the fresh instance publishes its console endpoint.
                time.sleep(0.1)
            raise TimeoutError('VM did not restart')

        def verify_host_placement():
            # Startup records capture the actual executing host CPU, not a
            # requested affinity or a scheduler queue assignment. Group by VM
            # incarnation so a reboot cannot fabricate distinct placement.
            session.log.flush()
            records = re.findall(
                rb'HypeR: vCPU (\d+) running as scheduler thread \d+ '
                rb'on guarded stack [^;]+; host CPU (\d+); '
                rb'VM VmId \{ slot: (\d+), generation: (\d+) \}',
                Path(logfile).read_bytes())
            instances = {}
            for cpu, host, slot, generation in records:
                instances.setdefault((slot, generation), {})[int(cpu)] = int(host)
            expected = min(host_cpus, guest_cpus)
            for placements in instances.values():
                if set(placements) != set(range(guest_cpus)):
                    continue
                hosts = set(placements.values())
                if any(cpu >= host_cpus for cpu in hosts):
                    raise RuntimeError(f'invalid host CPU placement: {placements}')
                if len(hosts) < expected:
                    raise RuntimeError(f'guest CPUs collapsed onto {len(hosts)} host CPUs: '
                                       f'{placements}; expected {expected}')
                return
            raise RuntimeError('missing complete per-vCPU host placement records')

        def online(token, expected):
            send(f'printf "{token}="; /bin/busybox cat /sys/devices/system/cpu/online'.encode())
            await_text(rb'\n' + token.encode() + b'=' + re.escape(expected.encode()) + rb'\n')
            await_text(rb'~ # ')

        def migrate_guest_cpus(cycle):
            if host_cpus < 2:
                return
            # Leave a guest timer pending across detachment and migration.
            # The kernel self-test separately migrates a guest that never WFI's.
            marker = f'/tmp/hyper-migration-{cycle}'
            send(f'(sleep 2; echo TIMER_OK > {marker}) &'.encode())
            await_text(rb'~ # ')
            session.send(b'\x1dd')
            await_text(rb'hyper-sh\$ ')
            for cpu in range(guest_cpus):
                # Also share one physical CPU to exercise virtual hardware
                # ownership when several vCPUs of the same VM time-slice.
                target = 0 if cycle == 1 else (cpu + cycle + 1) % host_cpus
                send(f'vmm affinity alpine {cpu} {target}'.encode())
                await_text(fr'vCPU {cpu}: affinity accepted; inspect vmm status for placement\n'.encode())
                await_text(rb'hyper-sh\$ ')
                deadline = time.monotonic() + 30
                while True:
                    send(b'vmm status alpine')
                    placement = await_text(fr'vCPU {cpu}: pCPU [^\n]*\n'.encode())
                    await_text(rb'hyper-sh\$ ')
                    if placement == f'vCPU {cpu}: pCPU {target}\n'.encode():
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f'vCPU {cpu} migration did not complete: {placement!r}')
                    time.sleep(0.1)
            attach()
            send(f'while [ ! -f {marker} ]; do sleep 1; done; cat {marker}'.encode())
            await_text(rb'\nTIMER_OK\n')
            await_text(rb'~ # ')

        def wait_state(expected):
            for _ in range(90):
                send(b'vmm status alpine')
                status = await_text(rb'alpine\s+(?:starting|running|stopping|stopped|failed)')
                await_text(rb'hyper-sh\$ ')
                if status.endswith(expected):
                    return
                if status.endswith(b'failed') and expected != b'failed':
                    raise RuntimeError('VM unexpectedly failed')
                time.sleep(0.1)
            raise TimeoutError(f'VM did not reach {expected!r}')

        def memory_owners():
            send(b'free --bytes')
            line = await_text(rb'Owners:[^\n]*user=\d+ B guest=\d+ B[^\n]*\n')
            await_text(rb'hyper-sh\$ ')
            return tuple(map(int, re.search(rb'user=(\d+) B guest=(\d+) B', line).groups()))

        try:
            await_text(rb'HypeR session: console ready')
            await_text(rb'hyper-sh\$ ')
            attach()
            for cycle in range(3):
                online(f'SMP_ONLINE_{cycle}', f'0-{guest_cpus - 1}')
                if cycle == 0:
                    verify_host_placement()
                migrate_guest_cpus(cycle)
                online(f'SMP_MIGRATED_{cycle}', f'0-{guest_cpus - 1}')
                # Offline all secondaries, then bring them back repeatedly.
                # This exercises fresh PSCI contexts and per-vCPU timers/IPIs.
                for cpu in range(1, guest_cpus):
                    send(f'echo 0 > /sys/devices/system/cpu/cpu{cpu}/online'.encode())
                    await_text(rb'~ # ')
                online(f'SMP_OFFLINE_{cycle}', '0')
                for cpu in range(1, guest_cpus):
                    send(f'echo 1 > /sys/devices/system/cpu/cpu{cpu}/online'.encode())
                    await_text(rb'~ # ')
                online(f'SMP_REONLINE_{cycle}', f'0-{guest_cpus - 1}')
                # Run multiple independent tasks so Linux can exercise scheduler
                # IPIs and timer preemption while the console remains responsive.
                # The minimal Alpine initramfs has no taskset applet.
                send(('for c in ' + ' '.join(map(str, range(guest_cpus)))
                      + '; do /bin/busybox sh -c '
                      + "'i=0; while [ $i -lt 10000 ]; do i=$((i+1)); done; echo CPU_WORK_OK' & "
                      + '; done; wait; echo SMP_WORK_DONE').replace('& ;', '&').encode())
                for _ in range(guest_cpus):
                    await_text(rb'(?:^|\n)CPU_WORK_OK\n')
                await_text(rb'(?:^|\n)SMP_WORK_DONE\n')
                await_text(rb'~ # ')
            for _ in range(2):
                send(b'reboot')
                await_text(rb'\[vmm\] virtual machine disconnected')
                await_text(rb'hyper-sh\$ ')
                attach()
                online('SMP_AFTER_REBOOT', f'0-{guest_cpus - 1}')
            send(b'poweroff')
            await_text(rb'\[vmm\] virtual machine disconnected')
            await_text(rb'hyper-sh\$ ')
            wait_state(b'stopped')
            _user, guest = memory_owners()
            if guest != 0:
                raise RuntimeError('guest poweroff leaked guest pages')
            print(f'verified {guest_cpus} guest CPUs, migration, hotplug, two reboots, '
                  'and poweroff reclamation')
        except Exception:
            sys.stderr.write(pending[-16384:].decode(errors='replace'))
            raise


if __name__ == '__main__':
    main()
