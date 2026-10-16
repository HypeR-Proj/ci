#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Nested capability routes, concurrent lazy activation, stdio and bounded lifecycle."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import time

from session import Session, native_command
from shell_status import checked, status


def prepare(args):
    args.output.mkdir(parents=True, exist_ok=False)
    system = args.output / 'system-root'
    shutil.copytree(args.composition / 'system-root', system, symlinks=True)
    shutil.copytree(args.composition / 'native-system', args.output / 'native-system')
    # Configuration is policy, not a fixed directory inside the manager.
    declarations = system / 'etc/test-components'
    (system / 'etc/components').rename(declarations)
    manager = system / 'etc/hyper/components.json'
    config = json.loads(manager.read_text())
    config['configuration_directory'] = '/etc/test-components'
    manager.write_text(json.dumps(config))
    root_path = declarations / 'root.json'
    root = json.loads(root_path.read_text())
    policy_path = system / 'etc/hyper/startup.json'
    policy = json.loads(policy_path.read_text())
    # Omitting a domain service closes its old rendezvous instead of hanging vmm.
    root['children'] = [c for c in root['children'] if c['name'] != 'vm-manager']
    policy['applications'] = [a for a in policy['applications'] if a['component'] != '/vm-manager']
    (declarations / 'vm-manager.json').unlink()
    root['children'].append({'name': 'tests', 'component': 'tests'})
    # Native ABI CapabilityChannel with DUPLICATE|TRANSFER|WAIT|WRITE.
    observe = {'type': 'object', 'object_type': 14, 'rights': 39}
    root.setdefault('capabilities', []).append({'name': 'observe', 'kind': observe})
    root.setdefault('offer', []).append({'name': 'observe', 'to': 'tests', 'from': 'self', 'source': 'observe', 'kind': observe})
    policy['exports'] = [{'realm': '/', 'name': 'observe', 'source': 'component.observe'}]
    realm = {'name': 'tests', 'children': [], 'offer': []}
    protocol = {'type': 'protocol', 'protocol': 'test.Echo'}
    def component(name, mode, **options):
        realm['children'].append({'name': name, 'component': name})
        value = {'name': name, 'program': {'image': '/bin/service-probe', 'arguments': [mode], **options}}
        return value
    def write(value):
        (declarations / (value['name'] + '.json')).write_text(json.dumps(value))
    for name, mode in [('plain', 'plain'), ('ipc', 'ipc'), ('observer', 'observe')]:
        value = component(name, mode, startup='eager')
        if name == 'observer':
            value['use'] = [{'name': 'observe', 'kind': observe, 'purpose': 0x800c0003}]
            realm['offer'].append({'name': 'observe', 'to': name, 'from': 'parent', 'source': 'observe', 'kind': observe})
        write(value)
    for name, mode in [('server', 'echo'), ('silent', 'idle')]:
        value = component(name, mode)
        value['capabilities'] = [{'name': 'echo', 'kind': protocol}]
        value['expose'] = [{'name': 'echo', 'from': 'self', 'source': 'echo', 'kind': protocol}]
        write(value)
    for name in ('burst-a', 'burst-b'):
        value = component(name, 'journal-burst')
        value['program']['arguments'].append(name)
        write(value)
    for name, mode, provider in [('client', 'connect', 'server'), ('timeout', 'timeout', 'silent')]:
        value = component(name, mode)
        value['use'] = [{'name': 'echo', 'kind': protocol}]
        realm['offer'].append({'name': 'echo', 'to': name, 'from': '#' + provider, 'source': 'echo', 'kind': protocol})
        write(value)
    write(component('retry', 'fail', restart='on-failure', restart_delay_ms=100, restart_limit=2))
    for prepared in (False, True):
        for resident in (False, True):
            result = 'allowed' if resident else 'denied'
            name = ('prepared-' if prepared else '') + 'resident-' + result
            value = component(name, 'memory-lock', startup='eager', prepared=prepared, resident=resident)
            value['program']['arguments'].append(result)
            write(value)
            if prepared:
                policy['applications'].append({'component': '/tests/' + name, 'critical': True})
    write(realm)
    if args.mode == 'prepare-rejected-console':
        provider = next(p['component'] for p in policy['providers'] if p['role'] == 'console')
        declaration = next(c['component'] for c in root['children'] if '/' + c['name'] == provider)
        path = declarations / (declaration + '.json')
        console = json.loads(path.read_text())
        console['program']['image'] = '/svc/missing-console-raw'
        path.write_text(json.dumps(console))
    root_path.write_text(json.dumps(root))
    policy_path.write_text(json.dumps(policy))
    spec = importlib.util.spec_from_file_location('fixture', Path(__file__).with_name('prepare-native-fixture.py'))
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    fixture.publish(args.output.resolve(), (args.output / 'services.cpio').resolve(), args.packer, args.strip)


def run_rejected_console(args):
    with Session(native_command(args.qemu, args.image, args.initramfs), args.log) as vm:
        boot = vm.await_text(rb'hyper-sh\$ ', timeout=90)
        if (b'HypeR: startup incomplete' not in boot
                or b'provider adoption failed: bootstrap retirement disabled' not in boot
                or b'bootstrap retired; boot archive detached' in boot):
            raise AssertionError('console adoption failure lost diagnostics or retired live owners')
        vm.send(b'echo CONSOLE_REPLACEMENT_RECOVERY_OK\n')
        vm.await_text(rb'(?m)^CONSOLE_REPLACEMENT_RECOVERY_OK\n', timeout=15)
        vm.await_text(rb'hyper-sh\$ ', timeout=15)
    print(f'console replacement recovery passed: {args.log}')


def run(args):
    with Session(native_command(args.qemu, args.image, args.initramfs), args.log) as vm:
        boot = vm.await_text(rb'hyper-sh\$ ', timeout=180)
        start = boot.index(b'HypeR component manager /')
        release = boot.index(b'HypeR: startup complete')
        prompt = boot.index(b'hyper-sh$ ')
        if not start < release < prompt:
            raise AssertionError('startup display/console/shell handoff is out of order')
        for marker in (b'SERVICE_STDOUT_OK', b'SERVICE_STDERR_OK',
                       b'HypeR system-startup: core supervision adopted'):
            if marker not in boot[start:release]:
                raise AssertionError(f'service log was hidden until startup completed: {marker!r}')
        for marker in (b'/console', b'/journal', b'/session-manager'):
            if marker not in boot[start:release]:
                raise AssertionError(f'early display missed component startup: {marker!r}')
        if b'HypeR session-manager: session ended:' in boot:
            raise AssertionError(f'initial session needed recovery: {boot!r}')
        def command(text):
            vm.send(checked(text))
            output = vm.await_text(rb'hyper-sh\$ ', timeout=30)
            if status(output) != 0:
                raise AssertionError(f'{text}: {output!r}')
            return output
        def state(name, expected, instance=None):
            until = time.monotonic() + 30
            while time.monotonic() < until:
                output = command(f'componentctl status {name}')
                if re.search(re.escape(name.encode()) + rb'\s+' + expected.encode() + rb'\s', output):
                    if instance is None or re.search(re.escape(name.encode()) + rb'\s+' + expected.encode() + rb'\s+' + str(instance).encode() + rb'\s', output):
                        return output
                vm.pump(0.1)
            raise AssertionError(f'{name} did not reach {expected}: {output!r}')
        state('/tests/plain', 'completed')
        state('/tests/observer', 'completed')
        state('/tests/ipc', 'completed')
        if b'interactive-ready' not in command('componentctl status /session-manager'):
            raise AssertionError('interactive program did not report readiness')
        for prefix in ('', 'prepared-'):
            for result in ('allowed', 'denied'):
                path = '/tests/' + prefix + 'resident-' + result
                state(path, 'completed')
                marker = ('COMPONENT_MEMORY_LOCK_' + result.upper() + '_OK').encode()
                if marker not in command('journalctl -c ' + path):
                    raise AssertionError(f'{path}: memory residency authority mismatch')
        state('/tests/server', 'stopped', 0)
        vm.send(checked('vmm list'))
        unavailable = vm.await_text(rb'hyper-sh\$ ', timeout=10)
        if status(unavailable) == 0 or b'Status(Status(-15))' not in unavailable:
            raise AssertionError(f'absent manager did not close its rendezvous: {unavailable!r}')
        logs = command('journalctl -c /tests/plain')
        if b'Stdout: SERVICE_STDOUT_OK' not in logs or b'Stderr: SERVICE_STDERR_OK' not in logs:
            raise AssertionError(f'unadapted stdio not attributed: {logs!r}')
        if b'SERVICE_OBSERVER_DENIED_OK' not in command('journalctl -c /tests/observer'):
            raise AssertionError('read-only observer mutation was not rejected')
        if b'SERVICE_LARGE_REPLY_OK' not in command('journalctl -c /tests/ipc'):
            raise AssertionError('large IPC reply framing failed')
        command('componentctl start /tests/burst-a; componentctl start /tests/burst-b')
        for name in ('burst-a', 'burst-b'):
            state('/tests/' + name, 'completed')
            # Process completion does not imply journal EOF has been consumed.
            until = time.monotonic() + 30
            while True:
                logs = command('journalctl -c /tests/' + name + ' -n 256')
                records = re.findall(
                    rb'/tests/' + name.encode() + rb'#1 (Stdout|Stderr): JOURNAL_BURST (\S+) (out|err) (\d{3}|EOF)( \[partial\])?',
                    logs,
                )
                if len(records) == 194:
                    break
                if time.monotonic() >= until:
                    raise AssertionError(f'incomplete batched journal for {name}: {logs!r}')
                vm.pump(0.1)
            for stream, tag in ((b'Stdout', b'out'), (b'Stderr', b'err')):
                actual = [(label, kind, value, partial) for channel, label, kind, value, partial in records if channel == stream]
                expected = [(name.encode(), tag, f'{i:03}'.encode(), b'') for i in range(96)]
                expected.append((name.encode(), tag, b'EOF', b' [partial]'))
                if actual != expected:
                    raise AssertionError(f'batch order, framing or attribution changed: {actual!r}')
        command('componentctl routes /tests/client echo')
        for generation in (1, 2):
            command('componentctl start /tests/client')
            state('/tests/client', 'completed', generation)
            state('/tests/server', 'running', 1)
        logs = command('journalctl -c /tests/client')
        if logs.count(b'COMPONENT_CONCURRENT_CONNECT_OK') != 2 or logs.count(b'COMPONENT_UNDECLARED_DENIED_OK') != 2:
            raise AssertionError(f'concurrent scoped connections failed: {logs!r}')
        command('componentctl restart /tests/server')
        state('/tests/server', 'running', 2)
        command('componentctl start /tests/client')
        state('/tests/client', 'completed', 3)
        command('componentctl stop /tests/server')
        state('/tests/server', 'stopped', 2)
        command('componentctl start /tests/timeout')
        state('/tests/timeout', 'completed', 1)
        if b'COMPONENT_DEADLINE_OK' not in command('journalctl -c /tests/timeout'):
            raise AssertionError('unpublished provider did not respect connection deadline')
        command('componentctl stop /tests/silent')
        state('/tests/silent', 'stopped', 1)
        command('componentctl start /tests/retry')
        state('/tests/retry', 'failed', 3)
        logs = command('journalctl -c /tests/retry')
        if logs.count(b'Stderr: SERVICE_FAILURE_ATTEMPT') != 3:
            raise AssertionError(f'retry budget or attribution failed: {logs!r}')
        vm.send(checked('componentctl stop /session-manager'))
        denied = vm.await_text(rb'hyper-sh\$ ', timeout=10)
        if status(denied) == 0 or b'ownership retirement protocol' not in denied:
            raise AssertionError(f'stateful stop was not rejected: {denied!r}')
        command('echo COMPONENT_MANAGEMENT_OK')
    print(f'component management acceptance passed: {args.log}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    p = sub.add_parser('prepare', aliases=['prepare-rejected-console'])
    for name in ('composition', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--packer', required=True)
    p.add_argument('--strip', required=True)
    p = sub.add_parser('run', aliases=['run-rejected-console'])
    p.add_argument('--qemu', required=True)
    for name in ('image', 'initramfs', 'log'):
        p.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    {'prepare': prepare, 'run': run,
     'prepare-rejected-console': prepare, 'run-rejected-console': run_rejected_console}[args.mode](args)


if __name__ == '__main__':
    main()
