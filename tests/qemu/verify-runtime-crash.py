#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Use a test-runtime-crash image to prove repeated runtime-loss retirement."""
import os
import re
import selectors
import subprocess
import sys
import time


def main():
    qemu, image, initramfs, logfile = sys.argv[1:]
    command = [qemu, '-machine', 'virt,virtualization=on,gic-version=3,dtb-randomness=on',
               '-cpu', 'cortex-a72', '-smp', '4', '-m', '512M',
               '-nodefaults', '-display', 'none', '-serial', 'stdio', '-no-reboot',
               '-monitor', 'none', '-kernel', image, '-initrd', initramfs,
               '-append', 'earlycon=pl011,mmio32,0x09000000']
    with open(logfile, 'wb') as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        pending = bytearray()

        def await_text(pattern, timeout=40):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                match = re.search(pattern, pending)
                if match:
                    matched = match.group(0)
                    del pending[:match.end()]
                    return matched
                if process.poll() is not None:
                    remaining = process.stdout.read()
                    log.write(remaining)
                    log.flush()
                    pending.extend(remaining)
                    raise RuntimeError('QEMU exited with status ' + str(process.returncode)
                                       + ' before ' + repr(pattern) + ':\n'
                                       + pending[-8192:].decode(errors='replace'))
                for key, _ in selector.select(0.2):
                    data = os.read(key.fd, 65536)
                    log.write(data)
                    log.flush()
                    pending.extend(data.replace(b'\r', b''))
                    if b'HypeR: fatal' in pending or b'kernel panic' in pending:
                        raise RuntimeError('kernel failure')
            raise TimeoutError('waiting for ' + repr(pattern))

        def send(command):
            process.stdin.write(command + b'\n')
            process.stdin.flush()

        def memory_owners():
            send(b'free')
            line = await_text(rb'Owners:[^\n]*user=\d+ MiB guest=\d+ MiB[^\n]*\n')
            await_text(rb'hyper-sh\$ ')
            return tuple(map(int, re.search(rb'user=(\d+) MiB guest=(\d+) MiB', line).groups()))

        try:
            await_text(rb'HypeR session: console ready')
            # Initial-VM failure is deliberately boot-critical in init. End
            # that boot lease cleanly before testing isolated later instances.
            await_text(rb'HypeR: vCPU 0 running as scheduler thread')
            send(b'vmm stop')
            await_text(rb'HypeR init: initial VM stopped cleanly')
            send(b'echo HYPER_CRASH_TEST_READY')
            await_text(rb'HYPER_CRASH_TEST_READY\nhyper-sh\$ ')
            baseline_user, baseline_guest = memory_owners()
            if baseline_guest != 0:
                raise RuntimeError('initial guest pages not retired')
            send(b'vmm start')
            for cycle in range(5):
                # A fresh guest and runtime must start after every abrupt exit.
                await_text(rb'HypeR: vCPU 0 running as scheduler thread')
                send(b'vmm console')
                await_text(rb'\[vmm\] virtual machine disconnected')
                await_text(rb'hyper-sh\$ ')
                # The manager observes process teardown asynchronously.
                for _ in range(30):
                    send(b'vmm status')
                    await_text(rb'default\t(?:failed|stopping|running)')
                    # Match current output in the log, not a prior prompt.
                    await_text(rb'hyper-sh\$ ')
                    log.flush()
                    with open(logfile, 'rb') as source:
                        states = re.findall(rb'default\t(\w+)', source.read())
                    if states and states[-1] == b'failed':
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError('runtime failure did not retire instance')
                for _ in range(30):
                    user, guest = memory_owners()
                    if guest == 0 and user <= baseline_user + 2:
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError('VM/runtime memory did not return near idle baseline')
                if cycle != 4:
                    send(b'vmm start')
            send(b'echo HYPER_RUNTIME_CRASH_CLEANUP_OK')
            await_text(rb'HYPER_RUNTIME_CRASH_CLEANUP_OK\nhyper-sh\$ ')
            print('verified five runtime crashes, guest retirement, and fresh shared buffers')
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
