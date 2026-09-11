#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise Native file tools and independent named VM lifecycles."""
import os
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

        def await_text(pattern, timeout=60):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                match = re.search(pattern, pending)
                if match:
                    result = bytes(pending[:match.end()])
                    del pending[:match.end()]
                    return result
                if process.poll() is not None:
                    raise RuntimeError(f'QEMU exited with {process.returncode}')
                for key, _ in selector.select(0.2):
                    data = os.read(key.fd, 65536)
                    log.write(data)
                    log.flush()
                    pending.extend(data.replace(b'\r', b''))
                    if b'HypeR: fatal' in pending or b'kernel panic' in pending:
                        raise RuntimeError('kernel failure')
            raise TimeoutError(f'waiting for {pattern!r}: {bytes(pending[-4096:])!r}')

        def send(data):
            process.stdin.write(data)
            process.stdin.flush()

        def run(text, expected=None, failed=False, timeout=60):
            send(text.encode() + b'\n')
            output = await_text(rb'hyper-sh\$ ', timeout=timeout)
            if (b'sh: command failed' in output) != failed:
                raise AssertionError(f'{text}: unexpected exit status: {output!r}')
            if expected is not None and not re.search(expected, output):
                raise AssertionError(f'{text}: missing {expected!r}: {output!r}')
            return output

        def state(name, wanted, timeout=60):
            # Bound elapsed time, not the number of fast status commands.
            # Guest setup on a loaded CI runner can outlive 50 short polls.
            deadline = time.monotonic() + timeout
            output = b''
            while (remaining := deadline - time.monotonic()) > 0:
                output = run(f'vmm status {name}', timeout=remaining)
                if re.search(name.encode() + rb'\s+' + wanted.encode() + rb'\s', output):
                    return
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            raise AssertionError(f'{name} did not reach {wanted}: {output!r}')

        try:
            # Other services may log before the shell prints its first prompt.
            await_text(rb'HypeR session: console ready\n')
            await_text(rb'hyper-sh\$ ')
            if verify_vm:
                state('alpine', 'running')
            run('cat /etc/hyper/vms.json', rb'"format": "hyper.vm-config"')
            run('cat -n /etc/hyper/vms.json', rb'\n\s+1\t\{')
            run('cat /missing /etc/hyper/vms.json', rb'"virtual-machines"', failed=True)
            run('ls --bytes /bin/cat /etc/hyper/vms.json', rb'-rwxr-xr-x\s+\d+\s+/bin/cat')
            run('ls -1 /bin', rb'\ncat\n')
            run('ls /missing /etc/hyper', rb'vms.json', failed=True)
            for tool in ('mv', 'ln', 'rm', 'chmod', 'cp', 'mkdir', 'rmdir', 'touch'):
                run(f'{tool} --help', rb'Usage:')
            run('mkdir -p -m 750 /file-tools/source/sub /file-tools/outside')
            run('ls /file-tools/source', rb'drwxr-x---\s+-\s+sub/')
            run('cp /etc/hyper/vms.json /file-tools/source/data')
            run('cp /file-tools/source/data /file-tools/source/data', rb'same file', failed=True)
            run('ln /file-tools/source/data /file-tools/hard')
            run('cp /file-tools/source/data /file-tools/hard', rb'same file', failed=True)
            run('cat /file-tools/hard', rb'"format": "hyper.vm-config"')
            run('ln -s data /file-tools/source/relative')
            run('ln -s absent /file-tools/source/dangling')
            run('ln -s /file-tools/outside /file-tools/source/external')
            run('touch /file-tools/outside/keep')
            run('cp -R /file-tools/source /file-tools/copy')
            run('ls -1 /file-tools/copy', rb'\nrelative@\n')
            run('cat /file-tools/copy/relative', rb'"format": "hyper.vm-config"')
            run('cp -R /file-tools/source /file-tools/source/child', rb'into itself', failed=True)
            run('ln -s /file-tools/source/sub /file-tools/alias')
            run('cp -R /file-tools/source /file-tools/alias/child', rb'into itself', failed=True)
            run('mv /file-tools/copy/data /file-tools/copy/renamed')
            run('cat /file-tools/copy/data', failed=True)
            run('cat /file-tools/copy/renamed', rb'"format": "hyper.vm-config"')
            run('chmod 600 /file-tools/copy/renamed')
            run('ls /file-tools/copy/renamed', rb'-rw-------\s+.*renamed')
            run('chmod u+x,go+r /file-tools/copy/renamed')
            run('ls /file-tools/copy/renamed', rb'-rwxr--r--\s+.*renamed')
            run('chmod -R u+rwX,go-rwx /file-tools/copy')
            run('ls /file-tools/copy', rb'drwx------\s+-\s+sub/')
            run('touch -r /etc/hyper/vms.json /file-tools/copy/renamed /file-tools/copy')
            run('cat /file-tools/copy/renamed', rb'"format": "hyper.vm-config"')
            run('touch -c /file-tools/absent')
            run('cat /file-tools/absent', failed=True)
            run('mkdir /file-tools/empty')
            run('rmdir /file-tools/empty')
            run('rmdir /file-tools/source', failed=True)
            run('rm /file-tools/source', failed=True)
            run('rm -rf /', rb'refusing', failed=True)
            run('rm -rf /file-tools/source/.', rb'refusing', failed=True)
            run('ln -s /file-tools/outside /file-tools/unlink-only')
            run('rm -r /file-tools/unlink-only/')
            run('mkdir /file-tools/batch')
            run('cp -R /file-tools/source/sub/. /file-tools/batch')
            run('touch /file-tools/one /file-tools/two')
            run('mv /file-tools/one /file-tools/two /file-tools/batch')
            run('cp /file-tools/batch/one /file-tools/batch/two /file-tools/outside')
            run('ls -1 /file-tools/outside', rb'\none\ntwo\n')
            run('rm -r /file-tools/copy /file-tools/source')
            run('ls /file-tools/outside/keep', rb'keep')
            run('cat /file-tools/hard', rb'"format": "hyper.vm-config"')
            run('rm -f /file-tools/absent')
            run('rm -r /file-tools')
            run('ls /file-tools', failed=True)
            run('top -b -n 1 -d 0.1', rb'CPU: user-thread')
            run('free --bytes', rb'Mem:\s+\d+ B')
            run('ps --name shell', rb'process\s+\d+.*shell')
            run('handle --objects --kind process', rb'\sprocess\s')
            if verify_vm:
                run('vmm status missing', rb"does not exist", failed=True)
                run('vmm create scratch --image /missing', rb'cannot open image', failed=True)
                output = run('vmm list')
                assert b'scratch' not in output
                run('vmm create scratch --image /vm/alpine.itb', rb'accepted')
                run('vmm create scratch --image /vm/alpine.itb', rb'already exists', failed=True)
                run('vmm start scratch', rb'accepted')
                state('scratch', 'running')
                state('alpine', 'running')
                run('vmm delete scratch', rb'stop the VM', failed=True)
                send(b'vmm console scratch\n')
                await_text(rb'Connected to scratch\.')
                # Validate the guest launched through the CLI, not a kernel boot
                # shortcut or another VM's retained output.
                await_text(rb'HypeR guest: repeated timer wakeups passed')
                await_text(rb'~ # ')
                send(b'\x1b[1;1Recho HYPER_CLI_GUEST_OK\n')
                await_text(rb'\nHYPER_CLI_GUEST_OK\n')
                send(b'\x1dq')
                await_text(rb'\[vmm\] detached\nhyper-sh\$ ')
                run('vmm stop scratch', rb'accepted')
                state('scratch', 'stopped')
                state('alpine', 'running')
                run('vmm save /etc/hyper/saved.json', rb'Saved /etc/hyper/saved.json')
                run('vmm save /etc/hyper/saved.json', rb'already exists', failed=True)
                run('cat /etc/hyper/saved.json', rb'"name":\s*"scratch"')
                run('vmm delete scratch', rb'accepted')
                run('vmm load /etc/hyper/saved.json', rb'already exists', failed=True)
                assert b'scratch' not in run('vmm list')
                run('vmm stop alpine', rb'accepted')
                state('alpine', 'stopped')
                run('vmm delete alpine', rb'accepted')
                run('vmm load /etc/hyper/saved.json', rb'accepted')
                state('alpine', 'running')
                state('scratch', 'stopped')
                run('vmm restart alpine', rb'accepted')
                state('alpine', 'running')
            run('echo HYPER_APPS_OK', rb'\nHYPER_APPS_OK\nhyper-sh\$ ')
            print('verified Native file tools' +
                  (', named VM isolation, and config save/load' if verify_vm else ''))
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
