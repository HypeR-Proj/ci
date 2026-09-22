#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Prove runtime-loss retirement at deterministic guest power boundaries."""

import importlib.util
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time


spec = importlib.util.spec_from_file_location(
    'guest_smp', Path(__file__).with_name('verify-guest-smp.py'))
guest_smp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guest_smp)


def main():
    qemu, image, initramfs, logfile, mode = sys.argv[1:]
    if mode not in ('dormant', 'pending', 'powered-off'):
        raise ValueError('unknown power crash mode')
    command = [qemu, '-machine', os.environ.get(
        'QEMU_MACHINE', 'virt,virtualization=on,gic-version=3'),
        '-cpu', os.environ.get('QEMU_CPU', 'max'),
        '-smp', os.environ.get('QEMU_CPUS', '4'),
        '-m', os.environ.get('QEMU_MEMORY', '1G'),
        '-nodefaults', '-display', 'none', '-serial', 'stdio',
        '-monitor', 'none', '-no-reboot', '-kernel', image, '-initrd', initramfs,
        '-append', os.environ.get('QEMU_BOOTARGS', 'earlycon=pl011,mmio32,0x09000000')]
    Path(logfile).parent.mkdir(parents=True, exist_ok=True)
    with open(logfile, 'wb') as log, selectors.DefaultSelector() as selector:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
        selector.register(process.stdout, selectors.EVENT_READ)
        pending = bytearray()

        def await_text(pattern, timeout=90):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                match = re.search(pattern, pending)
                if match:
                    result = match.group(0)
                    del pending[:match.end()]
                    return result
                if process.poll() is not None:
                    raise RuntimeError(f'QEMU exited with {process.returncode}')
                for key, _ in selector.select(0.2):
                    data = os.read(key.fd, 65536)
                    if not data:
                        raise RuntimeError('QEMU output closed')
                    log.write(data)
                    log.flush()
                    guest_smp.append_console_output(pending, data)
            raise TimeoutError(f'waiting for {pattern!r}')

        def send(command):
            process.stdin.write(command + b'\n')
            process.stdin.flush()

        def owners():
            send(b'free --bytes')
            line = await_text(rb'Owners:[^\n]*user=\d+ B guest=\d+ B[^\n]*\n')
            await_text(rb'hyper-sh\$ ')
            return tuple(map(int, re.search(rb'user=(\d+) B guest=(\d+) B', line).groups()))

        def wait_state(expected):
            for _ in range(90):
                send(b'vmm status alpine')
                status = await_text(rb'alpine\s+(?:starting|running|stopping|stopped|failed)')
                await_text(rb'hyper-sh\$ ')
                if status.endswith(expected):
                    return
                if status.endswith(b'failed') and expected != b'failed':
                    raise RuntimeError('runtime failed before the selected power boundary')
                time.sleep(0.1)
            raise TimeoutError(f'VM did not reach {expected!r}')

        try:
            # Config publication and shell readiness may print in either order.
            await_text(rb'(?s)(?=.*HypeR session: console ready)'
                       rb'(?=.*HypeR init: VM configuration loaded; no autostart VMs)')
            send(b'echo HYPER_POWER_CRASH_READY')
            await_text(rb'\nHYPER_POWER_CRASH_READY\nhyper-sh\$ ')
            baseline_user, baseline_guest = owners()
            if baseline_guest != 0:
                raise RuntimeError('fixture unexpectedly autostarted a guest')
            for cycle in range(3):
                send(b'vmm start alpine')
                if mode == 'powered-off':
                    await_text(rb'hyper-sh\$ ')
                    wait_state(b'running')
                    send(b'vmm console alpine')
                    await_text(rb'~ # ', timeout=guest_smp.GUEST_BOOT_TIMEOUT_SECONDS)
                    send(b'printf "POWER_CRASH_ONLINE="; cat /sys/devices/system/cpu/online')
                    await_text(rb'\nPOWER_CRASH_ONLINE=0-3\n')
                    await_text(rb'~ # ')
                    send(b'echo 0 > /sys/devices/system/cpu/cpu1/online')
                await_text(b'HYPER_POWER_CRASH state=' + mode.encode() + rb'\n')
                if mode == 'powered-off':
                    await_text(rb'\[vmm\] virtual machine disconnected')
                    await_text(rb'hyper-sh\$ ')
                wait_state(b'failed')
                for _ in range(90):
                    user, guest = owners()
                    if guest == 0 and user <= baseline_user + 2 * 1024 * 1024:
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError(f'{mode} cycle {cycle} leaked VM/runtime memory')
            print(f'verified three {mode} runtime deaths, reclamation and restart')
        except Exception:
            sys.stderr.write(pending[-16384:].decode(errors='replace'))
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdin.close()
            process.stdout.close()


if __name__ == '__main__':
    main()
