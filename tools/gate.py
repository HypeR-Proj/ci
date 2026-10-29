#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Publish system verification only for the exact candidate that was tested."""

import argparse
import json
from pathlib import Path
import subprocess

from workspace import ORG, api, verify_current
from configure import APP_ID


def write_check(repository, payload, identifier=None):
    endpoint = f'repos/{ORG}/{repository}/check-runs' + (f'/{identifier}' if identifier else '')
    response = subprocess.check_output(
        ['gh', 'api', endpoint, '--method', 'PATCH' if identifier else 'POST', '--input', '-'],
        input=json.dumps(payload), text=True)
    return json.loads(response)['id']


def publish(snapshot, outcome, url):
    applied = set()
    if outcome == 'success':
        applied = verify_current(snapshot)
    for repository, candidate in snapshot['projects'].items():
        if 'pr' not in candidate or repository in applied:
            continue
        payload = {'name': 'system', 'head_sha': candidate['head'], 'details_url': url,
                   'external_id': snapshot['identity'],
                   'output': {'title': 'HypeR system verification',
                              'summary': 'Candidate ' + snapshot['identity'],
                              'text': json.dumps(snapshot, sort_keys=True)}}
        identifier = None
        if outcome == 'pending':
            payload['status'] = 'queued'
        else:
            payload.update(status='completed', conclusion=outcome)
            page = 1
            matches = []
            while True:
                checks = api(f"repos/{ORG}/{repository}/commits/{candidate['head']}/check-runs?check_name=system&filter=all&per_page=100&page={page}")['check_runs']
                matches.extend(c['id'] for c in checks if c['app']['id'] == APP_ID
                               and c.get('external_id') == snapshot['identity'] and c.get('details_url') == url)
                if len(checks) < 100:
                    break
                page += 1
            if not matches:
                raise ValueError(f'{repository}: no pending system check for this run')
            identifier = max(matches)
            del payload['head_sha']
        write_check(repository, payload, identifier)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--outcome', choices=['pending', 'success', 'failure', 'cancelled'], required=True)
    parser.add_argument('--url', required=True)
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text())
    try:
        publish(snapshot, args.outcome, args.url)
    except ValueError:
        if args.outcome == 'success':
            publish(snapshot, 'failure', args.url)
        raise


if __name__ == '__main__':
    main()
