#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Authorize manual system runs on trusted main; previews only consume evidence."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import time
import zipfile

from authorization import require_org_member
from topic_store import Store
from workspace import ORG, api

REPOSITORY = f'{ORG}/ci'
WORKFLOW = '.github/workflows/system.yml'
AUTHORIZER = '.github/workflows/system-access.yml'
BOT = 'hyper-ci-bot[bot]'


def artifact_name(run_id, attempt):
    return f'system-authorization-{int(run_id)}-{int(attempt)}'


def authenticated_run(run_id, attempt):
    run = api(f'repos/{REPOSITORY}/actions/runs/{int(run_id)}')
    if (run['event'] != 'workflow_dispatch' or run['path'] != WORKFLOW
            or run['run_attempt'] != attempt or run['id'] != run_id):
        raise ValueError('System authorization belongs to another workflow or attempt')
    return run


def requester(run, state=None):
    actor = run['triggering_actor']
    if actor['login'] != BOT or actor['type'] != 'Bot':
        return actor
    match = re.fullmatch(r'System (?:verification|preview): (.+) \[([A-Za-z0-9_.-]+-[0-9]+)\]',
                         run['display_title'])
    if not match:
        raise ValueError('Bot system runs require a recorded organization-member request')
    state = Store().data if state is None else state
    record = state['requests'].get(match[2], {})
    request = record.get('request', {})
    if (record.get('phase') not in ('dispatching', 'done')
            or request.get('action') != 'verify' or request.get('target') != match[1]):
        raise ValueError('Bot dispatch does not match an authenticated verification request')
    return {'login': request['actor']}


def authorize(run_id, attempt):
    evidence = {'run_id': run_id, 'attempt': attempt, 'allowed': False}
    try:
        run = authenticated_run(run_id, attempt)
        actor = requester(run)
        member = require_org_member(actor['login'])
        if 'id' in actor and actor['id'] != member['id']:
            raise ValueError('Workflow actor identity changed')
        evidence.update(allowed=True, actor=member['login'], actor_id=member['id'],
                        triggering_actor_id=run['triggering_actor']['id'])
    except (ValueError, KeyError, subprocess.CalledProcessError) as error:
        evidence['reason'] = str(error)
    return evidence


def trusted_artifact(artifact, producer, name):
    return (artifact.get('name') == name and not artifact.get('expired')
            and artifact.get('size_in_bytes', 0) <= 64 * 1024
            and producer['event'] == 'workflow_run' and producer['path'] == AUTHORIZER
            and producer['head_branch'] == 'main' and producer['status'] == 'completed'
            and producer['conclusion'] == 'success'
            and artifact['workflow_run']['id'] == producer['id'])


def read_evidence(artifact, run):
    data = subprocess.check_output(['gh', 'api',
        f'repos/{REPOSITORY}/actions/artifacts/{artifact["id"]}/zip'])
    if artifact.get('digest') != 'sha256:' + hashlib.sha256(data).hexdigest():
        raise ValueError('System authorization artifact digest does not match')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.namelist() != ['authorization.json'] or archive.getinfo('authorization.json').file_size > 64 * 1024:
            raise ValueError('Invalid system authorization artifact contents')
        evidence = json.loads(archive.read('authorization.json'))
    if (evidence['run_id'] != run['id'] or evidence['attempt'] != run['run_attempt']):
        raise ValueError('System authorization belongs to another run or rerun')
    if evidence.get('allowed') is not True:
        raise ValueError(evidence.get('reason', 'System verification request was denied'))
    if evidence.get('triggering_actor_id') != run['triggering_actor']['id']:
        raise ValueError('System authorization belongs to another triggering actor')
    return evidence


def wait_for_authorization(run_id, attempt, timeout=600):
    run = authenticated_run(run_id, attempt)
    name = artifact_name(run_id, attempt)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = api(f'repos/{REPOSITORY}/actions/artifacts?name={name}&per_page=100')
        for artifact in sorted(response['artifacts'], key=lambda row: row['id'], reverse=True):
            if not artifact.get('workflow_run') or artifact.get('name') != name:
                continue
            producer = api(f'repos/{REPOSITORY}/actions/runs/{artifact["workflow_run"]["id"]}')
            if trusted_artifact(artifact, producer, name):
                return read_evidence(artifact, run)
        time.sleep(5)
    raise ValueError('Timed out waiting for trusted organization-membership authorization')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('authorize', 'wait'))
    parser.add_argument('--run', type=int, required=True)
    parser.add_argument('--attempt', type=int, required=True)
    parser.add_argument('--output', type=Path, default=Path('authorization.json'))
    args = parser.parse_args()
    if args.command == 'authorize':
        result = authorize(args.run, args.attempt)
        args.output.write_text(json.dumps(result) + '\n')
        print('Authorized organization member' if result['allowed'] else 'Denied: ' + result['reason'])
    else:
        result = wait_for_authorization(args.run, args.attempt)
        print('System verification authorized for', result['actor'])


if __name__ == '__main__':
    main()
