#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Apply the approved repository settings without an administrator push bypass."""

import argparse
import json
import subprocess

ORG = 'HypeR-Proj'
APP_ID = 5526627
OWNER_LOGIN = 'roolrz'
OWNER_ID = 13791334
OWNER_TEAM_ID = 20445205
OWNER_TEAM = 'merge-codeowners'
STATE_ACTORS = {
    'topic-state-writer': [{'actor_id': APP_ID, 'actor_type': 'Integration', 'bypass_mode': 'always'}],
    'default-branch-history': [],
}


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
         'bypass_actors': [{'actor_id': OWNER_TEAM_ID, 'actor_type': 'Team',
                            'bypass_mode': 'pull_request'}],
         'rules': [
             {'type': 'pull_request', 'parameters': {
                 'required_approving_review_count': 0,
                 'dismiss_stale_reviews_on_push': True,
                 'require_code_owner_review': False,
                 'require_extra_approval_for_unattributed_changes': False,
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
         'bypass_actors': [{'actor_id': OWNER_TEAM_ID, 'actor_type': 'Team',
                            'bypass_mode': 'pull_request'},
                           {'actor_id': APP_ID, 'actor_type': 'Integration',
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


def verify_owner_team():
    team = api(f'orgs/{ORG}/teams/{OWNER_TEAM}')
    members = api(f'orgs/{ORG}/teams/{OWNER_TEAM}/members?per_page=100')
    if (team['id'] != OWNER_TEAM_ID or team.get('parent')
            or [(member['login'], member['id']) for member in members] != [(OWNER_LOGIN, OWNER_ID)]):
        raise ValueError('The merge authority team must contain only roolrz and have no parent')


def record_state_policy():
    # Scoped installation tokens cannot see bypass actors. Bind the lists
    # verified by an administrator to the exact ruleset revisions instead.
    endpoint = f'repos/{ORG}/ci/rulesets'
    rules = {row['name']: row for row in api(endpoint)}
    policy = {'version': 1, 'rules': {}}
    for name, actors in STATE_ACTORS.items():
        rule = api(endpoint + '/' + str(rules[name]['id']))
        if rule.get('bypass_actors') != actors:
            raise ValueError('administrator must verify the exact state bypass actors before polling')
        policy['rules'][name] = {key: rule[key] for key in ('id', 'updated_at', 'bypass_actors')}
    subprocess.run(['gh', 'variable', 'set', 'CI_TOPIC_STATE_POLICY', '--repo', f'{ORG}/ci',
                    '--env', 'topic-control', '--body', json.dumps(policy)], check=True)


def configure(name, state_only=False):
    if '/' in name or not name or name.startswith('.'):
        raise ValueError('expected a repository name within HypeR-Proj')
    endpoint = f'repos/{ORG}/{name}'
    if state_only and name != 'ci':
        raise ValueError('state-only setup applies only to ci')
    if not state_only:
        verify_owner_team()
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
    if name == 'ci':
        record_state_policy()
    print(f'{ORG}/{name}: ' + ('protected coordinator state' if state_only else 'CODEOWNER/App PR submission; immutable default history'))


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
