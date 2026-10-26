#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Validate privileged static trace capture and actual exported event payloads."""
import argparse
from pathlib import Path
import time

from command import Shell
from guest_console import append_console_output
from session import Session
from trace_artifacts import TraceArtifacts, boot_command
from trace_validation import validate_capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('qemu')
    parser.add_argument('image')
    parser.add_argument('initramfs')
    parser.add_argument('logfile', type=Path)
    parser.add_argument('--disk', type=Path, required=True)
    parser.add_argument('--board', type=Path, required=True)
    args = parser.parse_args()
    artifacts = TraceArtifacts(args.disk, args.board, args.logfile.with_suffix('.artifacts'))
    names = ('frozen.jsonl', 'later.jsonl', 'trace.json', 'ipc.json', 'command.jsonl', 'remote.json', 'failed.json', 'expiry.json')
    with Session(boot_command(args.qemu, args.image, args.initramfs, args.disk, args.board), args.logfile,
                 output_filter=append_console_output) as session:
        session.await_text(rb'HypeR: startup complete\n', timeout=240)
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
        shell.check('perf trace dump --format jsonl --no-names -o /tmp/frozen.jsonl')
        shell.run('perf trace dump --generation 0 -o /tmp/stale.json').require('stale generation', failed=True)
        shell.run('perf trace dump --fail-on-loss -o /tmp/loss.json').require('loss policy', failed=True)
        shell.check('echo TRACE_STOPPED; ps; free')
        shell.check('perf trace dump --format jsonl --no-names -o /tmp/later.jsonl')
        shell.check('perf trace record --duration-ms 1000 --categories scheduler,syscall,ipc --max-events 128 -o /tmp/trace.json')
        shell.run('perf trace dump -o /tmp/trace.json').require('overwrite requires opt-in', failed=True)
        # Bounded lossless workload: two hops, duplex queues, explicit parents,
        # real blocked waits, and repeated protocol IDs on distinct connections.
        # One bounded command excludes serial control traffic and keeps the
        # collector off live rings. Continuous recording is tested separately.
        ipc_result = shell.run('perf trace record --snapshot --duration-ms 30000 --categories ipc,scheduler --poll-ms 1 --no-names --fail-on-loss --max-events 16384 -o /tmp/ipc.json -- /bin/lifecycle-stress trace', timeout=120)
        # This checks exit semantics, not command latency. Leave room for slow
        # TCG runners; command completion still stops the recording immediately.
        shell.check('perf trace record --duration-ms 30000 --categories syscall --max-events 16384 --format jsonl -o /tmp/command.jsonl -- /bin/echo TRACE_COMMAND_OK')
        shell.run('perf trace record --duration-ms 1000 --max-events 32 -o /tmp/failed.json -- /bin/cat /missing-trace-input').require('workload failure', failed=True)
        if b'running=false' not in shell.check('perf trace status'):
            raise AssertionError('collector left recording enabled')
        # Export happens outside all measured intervals. Serial carries command
        # status only; no trace body is printed or hex-framed by test-report.
        remote_result = shell.run('perf trace record --snapshot --duration-ms 30000 --categories ipc,scheduler --no-names --poll-ms 1 --fail-on-loss --max-events 32768 -o /tmp/remote.json -- /bin/touch /data/trace-created', timeout=180)
        shell.check('perf trace record --snapshot --duration-ms 50 --categories syscall --no-names --fail-on-loss -o /tmp/expiry.json')
        TraceArtifacts.save(shell, names)
    files = artifacts.extract(session, names)
    # Preserve both raw files before propagating strict capture failures.
    ipc_result.require('bounded IPC snapshot')
    remote_result.require('bounded filesystem snapshot')
    validate_capture(files, args.logfile)
    print('Trace lifecycle, CPU filtering, loss and Perfetto export acceptance passed')


if __name__ == '__main__':
    main()
