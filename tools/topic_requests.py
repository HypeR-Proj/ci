#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Authenticate button artifacts against GitHub's workflow and actor metadata."""

import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

from configure import APP_ID
from topic_store import pages
from workspace import ORG, api

LISTENER = Path(__file__).with_name('topic-button.yml')
WORKFLOW = '.github/workflows/topic-button.yml'


def target_for(repository, pull):
    topics = sorted(label['name'] for label in pull['labels'] if label['name'].lower().startswith('topic:'))
    if len(topics) > 1:
        raise ValueError('a PR must have at most one topic label')
    if topics and (topics[0] == 'topic:' or len(topics[0]) > 100):
        raise ValueError('invalid topic label')
    if topics and topics[0] != topics[0].lower():
        raise ValueError('topic labels must use lower-case names consistently across repositories')
    return topics[0] if topics else f'{repository}#{pull["number"]}'


def permission(repository, actor):
    result = api(f'repos/{ORG}/{repository}/collaborators/{actor}/permission')
    if result['permission'] not in ('admin', 'write', 'maintain'):
        raise ValueError(f'{actor} needs write access to {repository}')
    return result['permission'] == 'admin'


def validate_event(repository, run, event, check, pull):
    if (run['event'] != 'check_run' or run.get('run_attempt', 1) != 1
            or run['status'] != 'completed' or run['conclusion'] != 'success'
            or run['path'] != WORKFLOW):
        raise ValueError('request must come from the trusted button workflow, first attempt')
    if (event['repository']['full_name'] != f'{ORG}/{repository}'
            or event['sender']['id'] != run['actor']['id']
            or run.get('triggering_actor', run['actor'])['id'] != run['actor']['id']):
        raise ValueError('button actor or repository does not match the workflow run')
    original = event['check_run']
    if (original['id'] != check['id'] or original['app']['id'] != APP_ID
            or check['app']['id'] != APP_ID or check['name'] != 'topic'
            or check['external_id'] != f'topic:{repository}#{pull["number"]}'
            or original['head_sha'] != pull['head']['sha']
            or check['head_sha'] != pull['head']['sha']):
        raise ValueError('button belongs to another App, PR or superseded commit')
    if pull['state'] != 'open' or pull['base']['ref'] != 'main' or pull['draft']:
        raise ValueError('button requires an open, ready PR against main')
    target = target_for(repository, pull)
    if json.loads(original['output']['text'])['target'] != target:
        raise ValueError('topic changed after the button was displayed; refresh the PR')
    action = ('verify' if event['action'] == 'rerequested'
              else event.get('requested_action', {}).get('identifier'))
    if event['action'] not in ('rerequested', 'requested_action') or action not in ('verify', 'submit'):
        raise ValueError('unsupported topic button')
    return {'action': action, 'target': target, 'actor': run['actor']['login'],
            'repository': repository, 'pr': pull['number'], 'head': pull['head']['sha'],
            'url': run['html_url']}


def receive(repository, run):
    source = api(f'repos/{ORG}/{repository}/contents/{WORKFLOW}?ref={run["head_sha"]}')
    if base64.b64decode(source['content']) != LISTENER.read_bytes():
        raise ValueError('button workflow differs from the trusted credential-free listener')
    artifacts = list(pages(f'repos/{ORG}/{repository}/actions/runs/{run["id"]}/artifacts', 'artifacts'))
    matches = [item for item in artifacts if item['name'] == 'topic-request' and not item['expired']]
    if len(matches) != 1:
        raise ValueError('missing or ambiguous button request artifact')
    artifact = matches[0]
    if artifact['size_in_bytes'] > 1024 * 1024:
        raise ValueError('oversized button artifact')
    data = subprocess.check_output(['gh', 'api',
        f'repos/{ORG}/{repository}/actions/artifacts/{artifact["id"]}/zip'])
    if artifact.get('digest') != 'sha256:' + hashlib.sha256(data).hexdigest():
        raise ValueError('button artifact digest mismatch')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.namelist() != ['event.json'] or archive.getinfo('event.json').file_size > 1024 * 1024:
            raise ValueError('invalid request archive')
        event = json.loads(archive.read('event.json'))
    check = api(f'repos/{ORG}/{repository}/check-runs/{int(event["check_run"]["id"])}')
    prefix = f'topic:{repository}#'
    if not (check.get('external_id') or '').startswith(prefix):
        raise ValueError('invalid button PR identity')
    number = int(check['external_id'][len(prefix):])
    pull = api(f'repos/{ORG}/{repository}/pulls/{number}')
    request = validate_event(repository, run, event, check, pull)
    permission(repository, request['actor'])
    return request
