# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Validate complete raw trace artifacts on the host after the disk is fenced."""

import json


def complete(payload):
    metadata = payload['metadata']
    return (metadata['cross_cpu_spans'] and not metadata['limited']
            and not any(row['overwritten'] or row['incomplete'] for row in metadata['losses']))


def validate_remote(payload):
    if not complete(payload):
        raise AssertionError('remote RPC capture is incomplete')
    events = [event for event in payload['traceEvents'] if event['ph'] == 'i']
    clients = [e for e in events if e['name'] == 'rpc_client_begin']
    servers = {(e['args']['connection'], e['args']['request']) for e in events
               if e['name'] == 'rpc_server_begin'}
    # Direct kernel publication bypasses the byte-channel syscall wrapper.
    publications = {(int(e['args']['queue'], 16) & ~1, e['args']['thread'])
                    for e in events if e['name'] == 'ipc_enqueue'}
    if not any(client['args']['operation'] in (5, 10)
               and (client['args']['connection'], client['args']['request']) in servers
               and (int(client['args']['connection'], 16), client['args']['thread']) in publications
               for client in clients):
        raise AssertionError('kernel-originated FAT mutation lacks matching core/server observations')
    if not any(e.get('flow_out') and e['name'] == 'RPC dispatch' for e in payload['traceEvents']):
        raise AssertionError('no remote filesystem dispatch flow')


def validate_capture(files, logfile):
    frozen = files['frozen.jsonl'].read_bytes()
    rows = [json.loads(line) for line in frozen.splitlines() if line.strip()]
    if not rows[1:-1] or any(event['cpu'] != 0 for event in rows[1:-1]):
        raise AssertionError('empty trace or CPU filter ignored')
    first_generation = rows[0]['generation']
    if not rows[-1]['loss']:
        raise AssertionError('tiny ring did not report overwritten events')
    if files['later.jsonl'].read_bytes() != frozen:
        raise AssertionError('stopped records changed')
    payload = json.loads(files['trace.json'].read_bytes())
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
    logfile.with_suffix('.json').write_bytes(files['trace.json'].read_bytes())
    ipc = json.loads(files['ipc.json'].read_bytes())
    if not complete(ipc):
        raise AssertionError('IPC workload lost the complete causal stream')
    names = {event['name'] for event in ipc['traceEvents']}
    required = {'ipc_enqueue', 'ipc_dequeue', 'rpc_client_begin', 'rpc_server_begin',
                'rpc_parent', 'rpc_client_end', 'rpc_server_end', 'RPC round trip', 'RPC handler'}
    if not required <= names:
        raise AssertionError(f'missing causal boundaries: {required - names}')
    parents = [event['args'] for event in ipc['traceEvents'] if event['name'] == 'rpc_parent']
    connections = {parent[key] for parent in parents for key in ('connection', 'parent_connection')}
    if len(parents) != 4 or len(connections) != 2:
        raise AssertionError('missing or ambiguous explicit two-hop workload')
    clients = {event['args']['thread'] for event in ipc['traceEvents']
               if event['name'] == 'rpc_client_begin' and event['args']['connection'] in connections}
    servers = {event['args']['thread'] for event in ipc['traceEvents']
               if event['name'] == 'rpc_server_begin' and event['args']['connection'] in connections}
    if len(clients) != 2 or len(servers) != 2 or len(clients | servers) != 3:
        raise AssertionError(f'RPC actor attribution does not match the three workload threads: {clients}, {servers}')
    flows = [event for event in ipc['traceEvents'] if event.get('flow_out')]
    for name in ('Message delivery', 'RPC dispatch', 'RPC parent', 'Wake to execution'):
        if not any(event['name'] == name for event in flows):
            raise AssertionError(f'missing observed causal flow: {name}')
    logfile.with_suffix('.ipc.json').write_bytes(files['ipc.json'].read_bytes())
    command_rows = [json.loads(line) for line in files['command.jsonl'].read_bytes().splitlines() if line.strip()]
    exits = [row['args'] for row in command_rows[1:-1]
             if row['event'] == 'syscall_exit' and row['args']['returned'] == 0]
    if not exits or any(row['status'] != 0 for row in exits):
        raise AssertionError(f'successful workload exit lost its status: {exits}')
    expiry = json.loads(files['expiry.json'].read_bytes())
    if not complete(expiry) or not {'syscall_enter', 'syscall_exit'} <= {e['name'] for e in expiry['traceEvents']}:
        raise AssertionError('deadline-only snapshot failed to drain complete frozen records')
    validate_remote(json.loads(files['remote.json'].read_bytes()))
