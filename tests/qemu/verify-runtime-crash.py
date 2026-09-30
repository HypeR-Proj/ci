#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Prove autostart guest isolation and repeated runtime-loss retirement."""
import argparse
import re
import time

from session import Session, native_command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('qemu', 'image', 'initramfs', 'logfile'):
        parser.add_argument(name)
    parser.add_argument('--isolation-only', action='store_true')
    args = parser.parse_args()
    command = native_command(args.qemu, args.image, args.initramfs)
    failures = (b'HypeR: fatal', b'kernel panic', b'HypeR init: critical service ',
                b'HypeR init: VM fleet configuration rejected',
                b'HypeR init: bootstrap failed')
    with Session(command, args.logfile, failures=failures) as session:
        await_text = session.await_text

        def send(command):
            session.send(command + b'\n')

        def run(command, timeout=60):
            send(command)
            output = await_text(rb'hyper-sh\$ ', timeout)
            if b'sh: command failed' in output:
                raise AssertionError(f'{command!r} failed: {output!r}')
            return output

        def wait_state(name, expected, timeout=60):
            deadline = time.monotonic() + timeout
            output = b''
            while (remaining := deadline - time.monotonic()) > 0:
                output = run(b'vmm status ' + name, remaining)
                if re.search(rb'\n' + name + rb'\s+' + expected + rb'\s', output):
                    # Metrics require a live response from the owning runtime,
                    # rather than only the manager's cached Running state.
                    if expected != b'running' or re.search(
                            rb'allocated VM backing: \d+ bytes', output):
                        return
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            raise TimeoutError(f'{name!r} did not reach {expected!r}: {output!r}')

        def memory_owners():
            # Exact bytes expose sub-MiB guest leaks hidden by display rounding.
            output = run(b'free --bytes')
            match = re.search(rb'Owners:[^\n]*user=(\d+) B guest=(\d+) B', output)
            if match is None:
                raise AssertionError(f'missing memory ownership: {output!r}')
            return tuple(map(int, match.groups()))

        def crash_alpine():
            send(b'vmm console alpine')
            await_text(rb'\[vmm\] virtual machine disconnected')
            await_text(rb'hyper-sh\$ ')
            wait_state(b'alpine', b'failed')

        # Admission and shell readiness may print in either order.
        await_text(rb'(?s)(?=.*HypeR session: console ready)'
                   rb'(?=.*HypeR init: VM fleet configured)')
        await_text(rb'hyper-sh\$ ')
        wait_state(b'alpine', b'running')
        wait_state(b'survivor', b'running')
        # Both definitions autostart. Attaching only alpine trips its runtime's
        # console-forwarding fault hook while survivor keeps its own runtime.
        crash_alpine()
        wait_state(b'survivor', b'running')
        output = run(b'echo HYPER_AUTOSTART_ISOLATION_OK')
        if b'\nHYPER_AUTOSTART_ISOLATION_OK\n' not in output:
            raise AssertionError('Native shell did not survive the autostart runtime failure')
        if args.isolation_only:
            print('verified autostart runtime failure leaves the other guest and init alive')
            return

        run(b'vmm stop survivor')
        wait_state(b'survivor', b'stopped')
        # Process exit and the manager's Stopped state can precede deferred
        # kernel reclamation. Wait for both guests before fixing the baseline.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            baseline_user, baseline_guest = memory_owners()
            if baseline_guest == 0:
                break
            time.sleep(0.1)
        else:
            raise AssertionError('autostart guest pages not retired')
        # Keep the five fresh-instance reclamation cycles in addition to the
        # first autostart failure, so the idle baseline cannot hide a repeat leak.
        for _ in range(5):
            run(b'vmm start alpine')
            wait_state(b'alpine', b'running')
            crash_alpine()
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                user, guest = memory_owners()
                if guest == 0 and user <= baseline_user + 2 * 1024 * 1024:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError('VM/runtime memory did not return near idle baseline')
        output = run(b'echo HYPER_RUNTIME_CRASH_CLEANUP_OK')
        if b'\nHYPER_RUNTIME_CRASH_CLEANUP_OK\n' not in output:
            raise AssertionError('Native shell did not survive repeated runtime failure')
        print('verified autostart isolation and five fresh runtime crashes with guest retirement')


if __name__ == '__main__':
    main()
