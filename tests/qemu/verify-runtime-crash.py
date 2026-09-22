#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Use a test-runtime-crash image to prove repeated runtime-loss retirement."""
import os
import re
import sys
import time

from session import Session


def main():
    qemu, image, initramfs, logfile = sys.argv[1:]
    command = [qemu, '-machine', os.environ.get('QEMU_MACHINE', 'virt,virtualization=on,gic-version=3,dtb-randomness=on'),
               '-cpu', os.environ.get('QEMU_CPU', 'max'),
               '-smp', os.environ.get('QEMU_CPUS', '4'),
               '-m', os.environ.get('QEMU_MEMORY', '1G'),
               '-nodefaults', '-display', 'none', '-serial', 'stdio', '-no-reboot',
               '-monitor', 'none', '-kernel', image, '-initrd', initramfs,
               '-append', os.environ.get('QEMU_BOOTARGS', 'earlycon=pl011,mmio32,0x09000000')]
    with Session(command, logfile) as session:
        def await_text(pattern, timeout=40):
            return session.await_text(pattern, timeout, match_only=True)

        def send(command):
            session.send(command + b"\n")

        def memory_owners():
            # Exact bytes keep reclamation checks independent of display units
            # and prevent sub-MiB guest leaks from rounding down to zero.
            send(b'free --bytes')
            line = await_text(rb'Owners:[^\n]*user=\d+ B guest=\d+ B[^\n]*\n')
            await_text(rb'hyper-sh\$ ')
            return tuple(map(int, re.search(rb'user=(\d+) B guest=(\d+) B', line).groups()))

        await_text(rb'HypeR session: console ready')
        # Initial-VM failure is deliberately boot-critical in init. End
        # that boot lease cleanly before testing isolated later instances.
        await_text(rb'HypeR: vCPU 0 running as scheduler thread')
        send(b'vmm stop alpine')
        await_text(rb'HypeR init: initial VM stopped cleanly')
        send(b'echo HYPER_CRASH_TEST_READY')
        await_text(rb'HYPER_CRASH_TEST_READY\nhyper-sh\$ ')
        baseline_user, baseline_guest = memory_owners()
        if baseline_guest != 0:
            raise RuntimeError('initial guest pages not retired')
        send(b'vmm start alpine')
        for cycle in range(5):
            # A fresh guest and runtime must start after every abrupt exit.
            await_text(rb'HypeR: vCPU 0 running as scheduler thread')
            send(b'vmm console alpine')
            await_text(rb'\[vmm\] virtual machine disconnected')
            await_text(rb'hyper-sh\$ ')
            # The manager observes process teardown asynchronously.
            for _ in range(30):
                send(b'vmm status alpine')
                await_text(rb'alpine\s+(?:failed|stopping|running)')
                # Match current output in the log, not a prior prompt.
                await_text(rb'hyper-sh\$ ')
                session.log.flush()
                with open(logfile, 'rb') as source:
                    states = re.findall(rb'alpine\s+(\w+)', source.read())
                if states and states[-1] == b'failed':
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError('runtime failure did not retire instance')
            for _ in range(30):
                user, guest = memory_owners()
                if guest == 0 and user <= baseline_user + 2 * 1024 * 1024:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError('VM/runtime memory did not return near idle baseline')
            if cycle != 4:
                send(b'vmm start alpine')
        send(b'echo HYPER_RUNTIME_CRASH_CLEANUP_OK')
        await_text(rb'HYPER_RUNTIME_CRASH_CLEANUP_OK\nhyper-sh\$ ')
        print('verified five runtime crashes, guest retirement, and fresh shared buffers')


if __name__ == '__main__':
    main()
