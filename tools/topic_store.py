#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Durable coordinator state; normal Git ref updates provide compare-and-swap."""

import base64
import json
import os
import subprocess

from workspace import ORG, api
from configure import STATE_ACTORS

REPOSITORY = f'{ORG}/ci'
BRANCH = 'topic-state'


def write(path, payload=None, method='POST'):
    arguments = ['gh', 'api', path, '--method', method]
    if payload is not None:
        arguments += ['--input', '-']
    result = subprocess.check_output(arguments, text=True, stderr=subprocess.PIPE,
                                     input=json.dumps(payload) if payload is not None else None)
    return json.loads(result) if result.strip() else None


def pages(path, key=None):
    separator = '&' if '?' in path else '?'
    page = 1
    while True:
        response = api(f'{path}{separator}per_page=100&page={page}')
        rows = response[key] if key else response
        yield from rows
        if len(rows) < 100:
            break
        page += 1


def verify_policy(rules, raw):
    if not raw:
        raise ValueError('provision CI_TOPIC_STATE_POLICY in the topic-control environment with configure.py')
    policy = json.loads(raw)
    if policy.get('version') != 1:
        raise ValueError('unsupported state protection policy')
    for rule in rules:
        actors = STATE_ACTORS[rule['name']]
        recorded = policy.get('rules', {}).get(rule['name'], {})
        if (recorded.get('id') != rule['id'] or recorded.get('updated_at') != rule['updated_at']
                or recorded.get('bypass_actors') != actors
                or ('bypass_actors' in rule and rule['bypass_actors'] != actors)):
            raise ValueError('state protections changed; an administrator must verify and reprovision them')


class Store:
    def __init__(self):
        rules = {row['name']: row for row in pages(f'repos/{REPOSITORY}/rulesets')}
        writer = rules.get('topic-state-writer')
        history = rules.get('default-branch-history')
        if not writer or not history:
            raise ValueError('protect topic-state with configure.py --apply --state-only ci before polling')
        writer = api(f'repos/{REPOSITORY}/rulesets/{writer["id"]}')
        history = api(f'repos/{REPOSITORY}/rulesets/{history["id"]}')
        verify_policy((writer, history), os.environ.get('CI_TOPIC_STATE_POLICY', ''))
        if (writer['enforcement'] != 'active' or history['enforcement'] != 'active'
                or {rule['type'] for rule in writer['rules']} != {'creation', 'update'}
                or not {'deletion', 'non_fast_forward'} <= {rule['type'] for rule in history['rules']}
                or any('refs/heads/topic-state' not in rule['conditions']['ref_name']['include']
                       or rule['conditions']['ref_name']['exclude'] for rule in (writer, history))):
            raise ValueError('topic-state must have App-only writes and non-bypassable immutable history')
        self.base = None
        try:
            self.base = api(f'repos/{REPOSITORY}/git/ref/heads/{BRANCH}')['object']['sha']
        except subprocess.CalledProcessError as error:
            if 'HTTP 404' not in (error.stderr or ''):
                raise
        self.data = self.read('state.json') if self.base else {
            'version': 1, 'requests': {}, 'active': None, 'retired': {}, 'messages': {}}
        if self.data['version'] != 1:
            raise ValueError('unsupported topic state version')

    def read(self, path):
        content = api(f'repos/{REPOSITORY}/contents/{path}?ref={self.base}')
        if not content.get('content'):
            content = api(f'repos/{REPOSITORY}/git/blobs/{content["sha"]}')
        return json.loads(base64.b64decode(content['content']))

    def save(self, files=None):
        files = {**(files or {}), 'state.json': self.data}
        payload = {'tree': [{'path': path, 'mode': '100644', 'type': 'blob',
                             'content': json.dumps(value, sort_keys=True, indent=2) + '\n'}
                            for path, value in sorted(files.items())]}
        if self.base:
            payload['base_tree'] = api(f'repos/{REPOSITORY}/git/commits/{self.base}')['tree']['sha']
        tree = write(f'repos/{REPOSITORY}/git/trees', payload)['sha']
        commit = write(f'repos/{REPOSITORY}/git/commits', {
            'message': 'Record topic coordinator progress', 'tree': tree,
            'parents': [self.base] if self.base else []})['sha']
        endpoint = f'repos/{REPOSITORY}/git/'
        try:
            if self.base:
                write(endpoint + f'refs/heads/{BRANCH}', {'sha': commit, 'force': False}, 'PATCH')
            else:
                write(endpoint + 'refs', {'ref': f'refs/heads/{BRANCH}', 'sha': commit})
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            # A transport failure may follow a successful update. Never force
            # the ref or overwrite a different coordinator's checkpoint.
            if api(endpoint + f'ref/heads/{BRANCH}')['object']['sha'] != commit:
                raise
        self.base = commit
