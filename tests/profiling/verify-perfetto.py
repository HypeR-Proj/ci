#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Import an exported timeline with an upstream Perfetto trace_processor binary.

No downloads, UI automation or synthetic Linux ftrace records. The JSON flow
count must agree with Perfetto's actual relational flow table, and the importer
must report no error/data-loss diagnostics.
"""

import argparse
import csv
import io
import json
from pathlib import Path
import subprocess


def verify(processor, trace):
    payload = json.loads(trace.read_text())
    events = payload['traceEvents']
    starts = [event for event in events if event.get('flow_out')]
    ends = [event for event in events if event.get('flow_in')]
    if not starts or sorted(e['bind_id'] for e in starts) != sorted(e['bind_id'] for e in ends):
        raise ValueError('expected nonempty, paired exported flows')
    query = """
    SELECT
      (SELECT count(*) FROM flow) AS flows,
      (SELECT count(*) FROM slice WHERE name = 'RPC round trip') AS rpc,
      (SELECT coalesce(sum(value), 0) FROM stats
       WHERE severity IN ('error', 'data_loss')) AS errors;
    """
    result = subprocess.run([str(processor), str(trace), '-Q', query],
                            capture_output=True, text=True, check=True, timeout=60)
    rows = list(csv.DictReader(io.StringIO(result.stdout.strip())))
    if len(rows) != 1:
        raise ValueError(f'unexpected trace_processor result: {result.stdout}')
    row = rows[0]
    expected_rpc = sum(e.get('ph') == 'X' and e['name'] == 'RPC round trip' for e in events)
    if int(row['flows']) != len(starts) or int(row['rpc']) != expected_rpc or int(row['errors']):
        raise ValueError(f'Perfetto import mismatch: {row}; expected flows={len(starts)}, RPC={expected_rpc}\n{result.stderr}')
    print(f'Perfetto import passed: {row["flows"]} causal flows, {row["rpc"]} RPC intervals, no importer errors')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    parser.add_argument('--processor', type=Path, required=True)
    args = parser.parse_args()
    verify(args.processor.resolve(), args.trace.resolve())


if __name__ == '__main__':
    main()
