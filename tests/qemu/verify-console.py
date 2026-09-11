#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise paced terminal input, stale readiness, and bidirectional pressure."""
import os
import random
import re
import selectors
import subprocess
import sys
import time


def main():
    qemu, image, initramfs, logfile = sys.argv[1:]
    verify_vm = os.environ.get("HYPER_TEST_VM", "1")
    if verify_vm not in ("0", "1"):
        raise ValueError("HYPER_TEST_VM must be 0 or 1")
    verify_vm = verify_vm == "1"
    command = [qemu, '-machine', os.environ.get('QEMU_MACHINE', 'virt,virtualization=on,gic-version=3'),
               '-cpu', os.environ.get('QEMU_CPU', 'max'),
               '-smp', os.environ.get('QEMU_CPUS', '4'),
               '-m', os.environ.get('QEMU_MEMORY', '512M'),
               '-nodefaults', '-display', 'none', '-serial', 'stdio', '-no-reboot',
               '-monitor', 'none', '-kernel', image, '-initrd', initramfs,
               '-append', os.environ.get('QEMU_BOOTARGS', 'earlycon=pl011,mmio32,0x09000000')]
    with open(logfile, 'wb') as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        pending = bytearray()
        rng = random.Random(761)

        def pump(seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                for key, _ in selector.select(min(0.02, max(0, deadline - time.monotonic()))):
                    data = os.read(key.fd, 65536)
                    log.write(data)
                    log.flush()
                    pending.extend(data.replace(b'\r', b''))
                if process.poll() is not None:
                    raise RuntimeError(f'QEMU exited with {process.returncode}')
                if b'HypeR: fatal' in pending or b'kernel panic' in pending:
                    raise RuntimeError('kernel failure')

        def await_text(pattern, timeout=10):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                match = re.search(pattern, pending)
                if match:
                    result = bytes(pending[:match.end()])
                    del pending[:match.end()]
                    return result
                pump(0.02)
            raise TimeoutError(f'waiting for {pattern!r}: {bytes(pending[-2048:])!r}')

        def send(data):
            process.stdin.write(data)
            process.stdin.flush()

        def typed(text):
            for byte in text.encode():
                send(bytes([byte]))
                pump(rng.uniform(0.015, 0.12))
                # Do not send a rescue key: it can accidentally wake a router
                # blocked on the wrong input and conceal the original fault.
                await_text(re.escape(bytes([byte])))
            send(b'\r')

        def run(text, expected, timeout=30):
            send(text.encode() + b'\n')
            output = await_text(rb'hyper-sh\$ ', timeout=timeout)
            if not re.search(expected, output) or b'sh: command failed' in output:
                raise AssertionError(f'{text}: {output!r}')

        try:
            # Independently scheduled services can log between session readiness
            # and the first prompt. Require both in order, not adjacent bytes.
            await_text(rb'HypeR session: console ready\n', timeout=60)
            await_text(rb'hyper-sh\$ ', timeout=60)
            pump(5)
            pending.clear()
            rounds = int(os.environ.get('CONSOLE_TYPED_ROUNDS', '40'))
            for index in range(rounds):
                token = f'CONSOLE_{index:03d}'
                typed(f'echo {token}')
                await_text(rb'\n' + token.encode() + rb'\nhyper-sh\$ ')
                pump(rng.uniform(0.01, 0.3))
            # Exercise the idle-to-input transition without a continuously
            # queued producer masking a missed notification.
            pump(3)
            typed('echo AFTER_IDLE')
            await_text(rb'\nAFTER_IDLE\nhyper-sh\$ ')
            for _ in range(8):
                for _ in range(200):
                    send(b'x' * 16)
                    pump(0.002)
                pump(0.3)
                send(b'\x03')
                await_text(rb'\^C\nhyper-sh\$ ')
                run('echo AFTER_BURST', rb'\nAFTER_BURST\n')
            send(b'top -d 0.1\n')
            await_text(rb'CPU: user-thread')
            pump(1)
            send(b'q')
            await_text(rb'hyper-sh\$ ')
            run('cat /etc/hyper/vms.json', rb'"hyper.vm-config"')
            # A surviving descendant may retain stdout; command termination
            # must drain available output without waiting for that child's EOF.
            run('/bin/std-test --child detached-output', rb'OUTPUT_OWNER_EXITED', timeout=5)
            if verify_vm:
                send(b'vmm console alpine\n')
                await_text(rb'Connected to alpine\.', timeout=30)
                await_text(rb'~ # ', timeout=60)
                # Each character must wake the sleeping runtime and echo without
                # a following key or periodic collection timeout to rescue it.
                for index in range(12):
                    token = f'GUEST_WAKE_{index:02d}'
                    typed(f'echo {token}')
                    await_text(rb'\n' + token.encode() + rb'\n~ # ')
                    pump(0.2)
                pump(3)
                typed('echo GUEST_AFTER_IDLE')
                await_text(rb'\nGUEST_AFTER_IDLE\n~ # ')
                send(b'\x1d')
                await_text(rb'd/q: detach, any other key: resume')
                pump(0.2)
                send(b'q')
                await_text(rb'\[vmm\] detached\nhyper-sh\$ ')
            typed('echo CONSOLE_OK')
            await_text(rb'\nCONSOLE_OK\nhyper-sh\$ ')
            print(f'verified {rounds} paced commands, idle input, burst recovery, and top return' +
                  (' with guest console wakeups' if verify_vm else ''))
        finally:
            selector.close()
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


if __name__ == '__main__':
    main()
