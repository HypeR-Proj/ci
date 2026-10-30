#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Apply the approved repository settings without an administrator push bypass."""

import argparse
import json
import subprocess

ORG = 'HypeR-Proj'
APP_ID = 5526627


def api(path, payload=None, method=None):
    args = ['gh', 'api', path]
    if method:
        args += ['--method', method]
    if payload is not None:
        args += ['--input', '-']
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            text=True, stdout=subprocess.PIPE, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def rulesets(repository=None):
    common = {'target': 'branch', 'enforcement': 'active',
              'conditions': {'ref_name': {'include': ['~DEFAULT_BRANCH'], 'exclude': []}}}
    result = [
        {**common, 'name': 'default-branch-history', 'bypass_actors': [],
         'rules': [{'type': 'deletion'}, {'type': 'non_fast_forward'}]},
        {**common, 'name': 'review-and-verification',
         'bypass_actors': [{'actor_id': 5, 'actor_type': 'RepositoryRole',
                            'bypass_mode': 'pull_request'},
                           {'actor_id': APP_ID, 'actor_type': 'Integration',
                            'bypass_mode': 'pull_request'}],
         'rules': [
             {'type': 'pull_request', 'parameters': {
                 'required_approving_review_count': 1,
                 'dismiss_stale_reviews_on_push': True,
                 'require_code_owner_review': True,
                 'require_last_push_approval': False,
                 'required_review_thread_resolution': True,
                 'allowed_merge_methods': ['squash']}},
             {'type': 'required_linear_history'},
             {'type': 'required_status_checks', 'parameters': {
                 'strict_required_status_checks_policy': False,
                 'do_not_enforce_on_create': False,
                 'required_status_checks': [
                     {'context': 'module', 'integration_id': 15368},
                     {'context': 'system', 'integration_id': APP_ID}]}}
        ]},
        {**common, 'name': 'topic-submission',
         'bypass_actors': [{'actor_id': APP_ID, 'actor_type': 'Integration',
                            'bypass_mode': 'pull_request'}],
         'rules': [{'type': 'update'}]}
    ]
    if repository == 'ci':
        result[0]['conditions'] = {'ref_name': {
            'include': ['~DEFAULT_BRANCH', 'refs/heads/topic-state'], 'exclude': []}}
        result.append({**common, 'name': 'topic-state-writer',
                       'conditions': {'ref_name': {'include': ['refs/heads/topic-state'], 'exclude': []}},
                       'bypass_actors': [{'actor_id': APP_ID, 'actor_type': 'Integration', 'bypass_mode': 'always'}],
                       'rules': [{'type': 'creation'}, {'type': 'update'}]})
    return result


def configure(name, state_only=False):
    if '/' in name or not name or name.startswith('.'):
        raise ValueError('expected a repository name within HypeR-Proj')
    endpoint = f'repos/{ORG}/{name}'
    if state_only and name != 'ci':
        raise ValueError('state-only setup applies only to ci')
    if not state_only:
        api(endpoint, {'allow_squash_merge': True, 'allow_merge_commit': False,
                       'allow_rebase_merge': False, 'delete_branch_on_merge': True}, 'PATCH')
        api(endpoint + '/private-vulnerability-reporting', method='PUT')
    existing = {item['name']: item['id'] for item in api(endpoint + '/rulesets')}
    for rule in rulesets(name):
        if state_only and rule['name'] not in ('default-branch-history', 'topic-state-writer'):
            continue
        identifier = existing.get(rule['name'])
        path = endpoint + '/rulesets' + (f'/{identifier}' if identifier else '')
        api(path, rule, 'PUT' if identifier else 'POST')
    print(f'{ORG}/{name}: ' + ('protected coordinator state' if state_only else 'coordinator-only PR submission; immutable default history'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', nargs='*')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--state-only', action='store_true', help='protect ci state before deploying the coordinator')
    args = parser.parse_args()
    if args.apply:
        for name in args.repository:
            configure(name, args.state_only)
    else:
        print(json.dumps(rulesets(), indent=2))


if __name__ == '__main__':
    main()
