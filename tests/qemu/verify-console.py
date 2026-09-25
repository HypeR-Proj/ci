#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise paced terminal input, stale readiness, and bidirectional pressure."""
import os
import random
import re
import sys

from session import Session
from guest_console import append_console_output


def console_lines(*lines):
    """Match exact terminal lines while allowing interleaved kernel records."""
    kernel_record = rb'<[0-7]>\[[ ]*[0-9]+\.[0-9]+\] HypeR: [^\n]*\n'
    boundary = rb'\n(?:' + kernel_record + rb')*'
    return rb'(?m)^' + boundary.join(re.escape(line) for line in lines)


def main():
    qemu, image, initramfs, logfile = sys.argv[1:]
    verify_vm = os.environ.get("HYPER_TEST_VM", "1")
    if verify_vm not in ("0", "1"):
        raise ValueError("HYPER_TEST_VM must be 0 or 1")
    verify_vm = verify_vm == "1"
    command = [qemu, '-machine', os.environ.get('QEMU_MACHINE', 'virt,virtualization=on,gic-version=3'),
               '-cpu', os.environ.get('QEMU_CPU', 'max'),
               '-smp', os.environ.get('QEMU_CPUS', '4'),
               '-m', os.environ.get('QEMU_MEMORY', '1G'),
               '-nodefaults', '-display', 'none', '-serial', 'stdio', '-no-reboot',
               '-monitor', 'none', '-kernel', image, '-initrd', initramfs,
               '-append', os.environ.get('QEMU_BOOTARGS', 'earlycon=pl011,mmio32,0x09000000')]
    with Session(command, logfile, output_filter=append_console_output) as session:
        pending = session.pending
        pump = session.pump
        send = session.send
        rng = random.Random(761)

        def await_text(pattern, timeout=10):
            return session.await_text(pattern, timeout)

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

        # Independently scheduled services can log between session readiness
        # and the first prompt. Require both in order, not adjacent bytes.
        await_text(rb'HypeR session: console ready\n', timeout=60)
        await_text(rb'hyper-sh\$ ', timeout=60)
        pump(5)
        pending.clear()
        # Deliberately send typeahead in one burst: a non-reading child
        # must never steal the following command into a disposable pipe.
        send(b'echo TYPEAHEAD_FIRST\necho TYPEAHEAD_SECOND\n')
        await_text(rb'(?m)^TYPEAHEAD_FIRST\n')
        await_text(console_lines(b'TYPEAHEAD_SECOND', b'hyper-sh$ '))
        # CRLF may be split by the hardware; EOF belongs only to cat.
        send(b'cat\r')
        pump(0.03)
        send(b'\nterminal-cat\r\n\x04echo AFTER_TERMINAL_EOF\r\n')
        await_text(rb'(?m)^terminal-cat\n')
        await_text(console_lines(b'AFTER_TERMINAL_EOF', b'hyper-sh$ '))
        send(b'/bin/std-test --child terminal-line\r\nterminal-line\r\n')
        await_text(console_lines(b'TERMINAL_LINE_OK', b'hyper-sh$ '))
        send(b'/bin/std-test --child terminal-inherit\ninherit-data\r\n\x04')
        await_text(console_lines(b'inherit-data', b'TERMINAL_INHERIT_OK', b'hyper-sh$ '))
        run('/bin/std-test --child binary-eof-byte', rb'BINARY_EOF_BYTE_OK')
        # The console service survives both a requested shell exit and EOF.
        for exit_input in (b'exit\n', b'\x04'):
            send(exit_input)
            await_text(rb'HypeR virtual console: shell exited; restarting\n')
            await_text(console_lines(b'HypeR session: console ready', b'hyper-sh$ '))
            run('echo AFTER_SHELL_RESTART', rb'\nAFTER_SHELL_RESTART\n')
        rounds = int(os.environ.get('CONSOLE_TYPED_ROUNDS', '40'))
        for index in range(rounds):
            token = f'CONSOLE_{index:03d}'
            typed(f'echo {token}')
            await_text(console_lines(token.encode(), b'hyper-sh$ '))
            pump(rng.uniform(0.01, 0.3))
        # Exercise the idle-to-input transition without a continuously
        # queued producer masking a missed notification.
        pump(3)
        typed('echo AFTER_IDLE')
        await_text(console_lines(b'AFTER_IDLE', b'hyper-sh$ '))
        for _ in range(8):
            for _ in range(200):
                send(b'x' * 16)
                pump(0.002)
            pump(0.3)
            send(b'\x03')
            await_text(rb'\^C\n')
            await_text(rb'hyper-sh\$ ')
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
            await_text(console_lines(b'[vmm] detached', b'hyper-sh$ '))
        typed('echo CONSOLE_OK')
        await_text(console_lines(b'CONSOLE_OK', b'hyper-sh$ '))
        print(f'verified {rounds} paced commands, idle input, burst recovery, and top return' +
              (' with guest console wakeups' if verify_vm else ''))


if __name__ == '__main__':
    main()
