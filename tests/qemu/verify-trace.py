#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Validate privileged static trace capture and actual exported event payloads."""
import argparse
import json
from pathlib import Path
import time

from command import Shell
from guest_console import append_console_output
from session import Session, native_command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('qemu')
    parser.add_argument('image')
    parser.add_argument('initramfs')
    parser.add_argument('logfile', type=Path)
    args = parser.parse_args()
    with Session(native_command(args.qemu, args.image, args.initramfs), args.logfile,
                 output_filter=append_console_output) as session:
        session.await_text(rb'HypeR session: console ready\n', timeout=180)
        session.await_text(rb'hyper-sh\$ ')
        shell = Shell(session)
        if b'running=false' not in shell.check('perf trace status'):
            raise AssertionError('tracing enabled at boot')
        shell.check('cp /bin/perf /tmp/unprivileged-perf')
        shell.run('/tmp/unprivileged-perf trace status').require('unprivileged trace', failed=True)
        shell.run('perf trace start --cpus 255').require('offline CPU', failed=True)
        shell.check('perf trace start --duration-ms 1000 --categories scheduler')
        shell.check('perf trace stop')
        shell.check('perf trace start --duration-ms 60000 --categories scheduler')
        session.pump(1.2)
        if b'running=true' not in shell.check('perf trace status'):
            raise AssertionError('cancelled deadline stopped the replacement capture')
        # Check exclusion while a long-lived capture is active. A separate
        # command can take longer than the short expiry test on a TCG runner.
        shell.run('perf trace start').require('concurrent capture', failed=True)
        shell.check('perf trace stop')
        # Use a tiny ring and CPU filter: this intentionally exercises overwrite.
        shell.check('perf trace start --duration-ms 1200 --buffer-records 64 --cpus 0 --categories scheduler,syscall,irq')
        deadline = time.monotonic() + 20
        while b'running=false' not in shell.check('perf trace status'):
            if time.monotonic() > deadline:
                raise AssertionError('trace did not stop autonomously')
            session.pump(0.1)
        shell.check('perf trace dump --format jsonl -o /tmp/frozen.jsonl')
        frozen = shell.check('cat /tmp/frozen.jsonl')
        rows = [json.loads(line) for line in frozen.splitlines() if line.strip()]
        if not rows[1:-1] or any(event['cpu'] != 0 for event in rows[1:-1]):
            raise AssertionError('empty trace or CPU filter ignored')
        first_generation = rows[0]['generation']
        if not rows[-1]['loss']:
            raise AssertionError('tiny ring did not report overwritten events')
        shell.run('perf trace dump --generation 0 -o /tmp/stale.json').require('stale generation', failed=True)
        shell.run('perf trace dump --fail-on-loss -o /tmp/loss.json').require('loss policy', failed=True)
        shell.check('echo TRACE_STOPPED; ps; free')
        shell.check('perf trace dump --format jsonl -o /tmp/later.jsonl')
        if shell.check('cat /tmp/later.jsonl') != frozen:
            raise AssertionError('stopped records changed')
        shell.check('perf trace record --duration-ms 1000 --categories scheduler,syscall,ipc --max-events 128 -o /tmp/trace.json')
        payload = json.loads(shell.check('cat /tmp/trace.json', timeout=120))
        if payload['metadata']['generation'] <= first_generation:
            raise AssertionError('capture generation reused')
        events = payload['traceEvents']
        observed = {event['name'] for event in events}
        if not {'sched_switch', 'syscall_enter', 'syscall_exit'} <= observed:
            raise AssertionError(f'missing real instrumentation: {observed}')
        metadata = payload['metadata']
        loss = metadata['limited'] or any(row['overwritten'] or row['incomplete'] for row in metadata['losses'])
        if loss and metadata['cross_cpu_spans']:
            raise AssertionError('lossy capture inferred cross-CPU durations')
        # The collector must not feed itself by tracing the trace-read method.
        if any(event['name'] == 'syscall_enter' and event.get('args', {}).get('operation') in (260, 261, 262)
               and event['args'].get('kind') == 19 for event in events):
            raise AssertionError('trace control recursively generated syscall events')
        args.logfile.with_suffix('.json').write_text(json.dumps(payload) + '\n')
        shell.run('perf trace dump -o /tmp/trace.json').require('overwrite requires opt-in', failed=True)
        # This checks exit semantics, not command latency. Leave room for slow
        # TCG runners; command completion still stops the recording immediately.
        shell.check('perf trace record --duration-ms 30000 --categories syscall --max-events 16384 --format jsonl -o /tmp/command.jsonl -- /bin/echo TRACE_COMMAND_OK')
        command_rows = [json.loads(line) for line in shell.check('cat /tmp/command.jsonl', timeout=120).splitlines() if line.strip()]
        exits = [row['args'] for row in command_rows[1:-1]
                 if row['event'] == 'syscall_exit' and row['args']['returned'] == 0]
        if not exits or any(row['status'] != 0 for row in exits):
            raise AssertionError(f'successful workload exit lost its status: {exits}')
        shell.run('perf trace record --duration-ms 1000 --max-events 32 -o /tmp/failed.json -- /bin/cat /missing-trace-input').require('workload failure', failed=True)
        if b'running=false' not in shell.check('perf trace status'):
            raise AssertionError('collector left recording enabled')
    print('Trace lifecycle, CPU filtering, loss and Perfetto export acceptance passed')


if __name__ == '__main__':
    main()
