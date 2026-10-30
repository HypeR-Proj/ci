#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Publish system verification only for the exact candidate that was tested."""

import argparse
import json
from pathlib import Path
import subprocess

from workspace import ORG, api, retry_github, verify_current
from configure import APP_ID


def matching_checks(repository, head, identity, url):
    page = 1
    matches = []
    while True:
        checks = api(f'repos/{ORG}/{repository}/commits/{head}/check-runs?check_name=system&filter=all&per_page=100&page={page}')['check_runs']
        matches.extend(c for c in checks if c['app']['id'] == APP_ID
                       and c.get('external_id') == identity and c.get('details_url') == url)
        if len(checks) < 100:
            return matches
        page += 1


def write_check(repository, payload, identifier=None):
    def write():
        if identifier is None:
            # A failed response does not prove that POST failed. Reconcile with
            # this exact run before creating anything, including on retries.
            matches = matching_checks(repository, payload['head_sha'],
                                      payload['external_id'], payload['details_url'])
            pending = [c['id'] for c in matches if c['status'] != 'completed']
            if pending:
                return max(pending)
        endpoint = f'repos/{ORG}/{repository}/check-runs' + (f'/{identifier}' if identifier else '')
        response = subprocess.check_output(
            ['gh', 'api', endpoint, '--method', 'PATCH' if identifier else 'POST', '--input', '-'],
            input=json.dumps(payload), text=True, stderr=subprocess.PIPE)
        return json.loads(response)['id']
    return retry_github(write)


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
                              'summary': 'Candidate ' + snapshot['identity']
                              + f'\n\n[Open PR Checks](https://github.com/{ORG}/{repository}/pull/{candidate["pr"]}/checks)'
                              + ' and select **topic** for **Run system CI** / **Submit topic**.',
                              'text': json.dumps(snapshot, sort_keys=True)}}
        identifier = None
        if outcome == 'pending':
            payload['status'] = 'queued'
        else:
            payload.update(status='completed', conclusion=outcome)
            matches = matching_checks(repository, candidate['head'], snapshot['identity'], url)
            if not matches:
                raise ValueError(f'{repository}: no pending system check for this run')
            identifier = max(c['id'] for c in matches)
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
