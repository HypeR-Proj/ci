#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise physical PMUv3 overflow, bounded capture, restart and symbolization."""
import argparse
import json
from pathlib import Path
import sys

from command import Shell
from guest_console import append_console_output
from session import Session, native_command

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/profiling'))
from flamegraph import Symbols, captures, svg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('qemu')
    parser.add_argument('image')
    parser.add_argument('initramfs')
    parser.add_argument('logfile', type=Path)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--nm', required=True)
    parser.add_argument('--unsupported', action='store_true')
    args = parser.parse_args()
    with Session(native_command(args.qemu, args.image, args.initramfs), args.logfile,
                 output_filter=append_console_output) as session:
        session.await_text(rb'HypeR session: console ready\n', timeout=180)
        session.await_text(rb'hyper-sh\$ ')
        shell = Shell(session)
        if args.unsupported:
            result = shell.run('perf status')
            result.require('perf status', failed=True)
            if b'not supported' not in result.output.lower() and b'NotSupported' not in result.output and b'Status(-4)' not in result.output:
                raise AssertionError(f'expected unsupported PMU: {result.output!r}')
            return
        shell.check('perf status')
        shell.check('perf start --seconds 5 --period 1000000')
        shell.run('perf start').require('second capture must be busy', failed=True)
        shell.run('perf start --period 1').require('unbounded sample rate must be rejected', failed=True)
        session.pump(2)
        shell.check('perf dump --output /tmp/pmu-wrap.jsonl')
        wrapped = shell.check('cat /tmp/pmu-wrap.jsonl')
        rows = [line for line in wrapped.decode().splitlines() if line.startswith('{')]
        _, metadata = captures(rows, lambda offset: f'0x{offset:x}')
        if metadata['overwritten'] == 0:
            raise AssertionError('bounded ring overwrite was not exercised')
        first_generation = json.loads(rows[0])['generation']
        shell.check('perf record --seconds 3 --period 100000000 --output /tmp/pmu.jsonl')
        output = shell.check('cat /tmp/pmu.jsonl')
        lines = [line for line in output.decode().splitlines() if line.startswith('{')]
        header = json.loads(lines[0])
        if header['generation'] <= first_generation:
            raise AssertionError('new capture reused an old generation')
        symbols = Symbols(args.kernel, args.nm, header['kernel'])
        stacks, metadata = captures(lines, symbols.resolve)
        rows = [json.loads(line) for line in lines[1:-1]]
        if {row['cpu'] for row in rows} != set(range(header['cpus'])):
            raise AssertionError('some CPUs never delivered a real PMU overflow')
        if not any(len(row['frames']) > 1 for row in rows):
            raise AssertionError('no kernel stack was unwound')
        if not any('hyper::' in stack for stack in stacks):
            raise AssertionError('kernel function names were not resolved')
        args.logfile.with_suffix('.jsonl').write_text('\n'.join(lines) + '\n')
        args.logfile.with_suffix('.svg').write_text(svg(stacks, metadata))
        args.logfile.with_suffix('.folded').write_text(''.join(f'{s} {n}\n' for s, n in sorted(stacks.items())))
        status = shell.check('perf status')
        if b'running=false armed=0x0' not in status:
            raise AssertionError(f'capture did not automatically disarm: {status!r}')
        shell.check('perf record --seconds 3 --output /tmp/command.jsonl -- /bin/echo PMU_COMMAND_OK')
        shell.run('perf record --seconds 3 --output /tmp/failed.jsonl -- /bin/cat /missing-pmu-input').require('failed workload', failed=True)
        shell.run('perf record --seconds 3 --output /missing-directory/pmu.jsonl -- /bin/echo discarded').require('failed export', failed=True)
        shell.check('perf stop')
        # A copy outside the shell's trusted tool path receives no PMU authority.
        shell.check('cp /bin/perf /tmp/unprivileged-perf')
        shell.run('/tmp/unprivileged-perf status').require('unprivileged PMU read', failed=True)
    print('PMUv3 overflow/capture/flamegraph acceptance passed')


if __name__ == '__main__':
    main()
