#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Prove remote filesystem RPC correlation beyond userspace IPC wrappers."""

import argparse
import json
from pathlib import Path

from command import Shell
from guest_console import append_console_output
from session import Session
from trace_artifacts import TraceArtifacts, boot_command
from trace_validation import validate_remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('qemu', 'image', 'initramfs', 'disk', 'board', 'log'):
        parser.add_argument('--' + argument, required=True)
    args = parser.parse_args()
    log = Path(args.log)
    artifacts = TraceArtifacts(args.disk, args.board, log.with_suffix('.artifacts'))
    with Session(boot_command(args.qemu, args.image, args.initramfs, args.disk, args.board),
                 log, output_filter=append_console_output) as session:
        session.await_text(rb'HypeR: startup complete\n', timeout=240)
        session.await_text(rb'hyper-sh\$ ')
        shell = Shell(session)
        capture_result = shell.run('perf trace record --raw-events --format chrome --snapshot --duration-ms 30000 --categories ipc,scheduler --no-names --poll-ms 1 --fail-on-loss --max-events 32768 -o /tmp/remote.json -- /bin/touch /data/trace-created', timeout=180)
        TraceArtifacts.save(shell, ('remote.json',))
    files = artifacts.extract(session, ('remote.json',))
    capture_result.require('bounded filesystem snapshot')
    validate_remote(json.loads(files['remote.json'].read_bytes()))
    log.with_suffix('.json').write_bytes(files['remote.json'].read_bytes())
    print('Remote filesystem RPC, kernel message commits and causal export passed')


if __name__ == '__main__':
    main()
