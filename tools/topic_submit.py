#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Submit a complete verified topic, checkpointing each guarded PR merge."""

import concurrent.futures
import json
import re

from configure import APP_ID
from topic_requests import permission, target_for
from topic_store import pages, write
from workspace import ORG, api, graphql, resolve, verify_current

ACTIONS_APP_ID = 15368


def latest_check(repository, head, name, app):
    checks = list(pages(f'repos/{ORG}/{repository}/commits/{head}/check-runs'
                        f'?check_name={name}&filter=all', 'check_runs'))
    matches = [check for check in checks if check['name'] == name and check['app']['id'] == app]
    if not matches:
        raise ValueError(f'{repository}: {name} check is missing')
    return max(matches, key=lambda check: check['id'])


def successful(check, repository):
    if check['status'] != 'completed' or check['conclusion'] != 'success':
        raise ValueError(f'{repository}: {check["name"]} has not passed')


def verified_snapshot(request):
    check = latest_check(request['repository'], request['head'], 'system', APP_ID)
    successful(check, request['repository'])
    snapshot = json.loads(check['output']['text'])
    if snapshot['target'] != request['target'] or snapshot['identity'] != check['external_id']:
        raise ValueError('system check verified another target')
    match = re.fullmatch(r'https://github.com/HypeR-Proj/ci/actions/runs/([0-9]+)', check['details_url'])
    if not match:
        raise ValueError('system check has no trusted workflow run')
    run = api(f'repos/{ORG}/ci/actions/runs/{match[1]}')
    if (run['status'] != 'completed' or run['conclusion'] != 'success'
            or run['event'] != 'workflow_dispatch' or run['head_branch'] != 'main'
            or run['path'] != '.github/workflows/system.yml'
            or run['head_sha'] != snapshot['projects']['ci']['base']):
        raise ValueError('system verification must finish successfully using the trusted main workflow')
    return snapshot


def review(repository, number):
    pull = graphql('query {repository(owner:' + json.dumps(ORG) + ',name:' + json.dumps(repository)
                   + '){pullRequest(number:' + str(number) + '){reviewDecision '
                   'reviewThreads(first:100){nodes{isResolved}pageInfo{hasNextPage}}}}}')['repository']['pullRequest']
    if pull['reviewDecision'] != 'APPROVED':
        raise ValueError(f'{repository}#{number}: CODEOWNER approval is required')
    threads = pull['reviewThreads']
    if threads['pageInfo']['hasNextPage'] or any(not thread['isResolved'] for thread in threads['nodes']):
        raise ValueError(f'{repository}#{number}: review conversations need resolution')


def preflight(snapshot, actor, force=False, repositories=None):
    applied = verify_current(snapshot)
    def inspect(item):
        repository, candidate = item
        administrator = permission(repository, actor)
        if force and not administrator:
            raise ValueError('force submission requires administrator access to every member repository')
        pull = api(f'repos/{ORG}/{repository}/pulls/{candidate["pr"]}')
        if pull['draft'] or not pull.get('mergeable'):
            raise ValueError(f'{repository}: PR is draft, conflicting, or mergeability is not ready')
        if target_for(repository, pull) != snapshot['target']:
            raise ValueError(f'{repository}: topic membership changed')
        if 'merge_tree' not in candidate:
            raise ValueError(f'{repository}: verified merge tree is missing')
        if not force:
            successful(latest_check(repository, candidate['head'], 'module', ACTIONS_APP_ID), repository)
            check = latest_check(repository, candidate['head'], 'system', APP_ID)
            successful(check, repository)
            if check['external_id'] != snapshot['identity']:
                raise ValueError(f'{repository}: system checks did not verify the same complete topic')
        if not administrator:
            review(repository, candidate['pr'])
    pending = [(name, candidate) for name, candidate in snapshot['projects'].items()
               if 'pr' in candidate and name not in applied
               and (repositories is None or name in repositories)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(inspect, pending))
    return applied


def begin(store, manifest, request, force=False):
    active = store.data['active']
    if active:
        if active['snapshot']['target'] != request['target']:
            raise ValueError('another topic submission must finish or be recovered first')
        if active['force'] != force or active['actor'] != request['actor']:
            raise ValueError('resume the active submission with the original actor and force setting')
        active['phase'] = 'running'
        active['error'] = None
        store.save()
        return
    snapshot = resolve(manifest, request['target']) if force else verified_snapshot(request)
    preflight(snapshot, request['actor'], force)
    store.data['active'] = {'snapshot': snapshot, 'actor': request['actor'], 'force': force,
                            'phase': 'running', 'inflight': None, 'error': None, 'merged': []}
    store.save()


def merge(repository, candidate):
    return write(f'repos/{ORG}/{repository}/pulls/{candidate["pr"]}/merge', {
        'sha': candidate['head'], 'merge_method': 'squash'}, 'PUT')


def reconcile_inflight(active):
    """Recover a lost merge response, including GitHub's partially updated PR state."""
    repository = active['inflight']
    if not repository:
        return
    candidate = active['snapshot']['projects'][repository]
    pull = api(f'repos/{ORG}/{repository}/pulls/{candidate["pr"]}')
    if pull['merged']:
        return
    if pull['state'] != 'open' or pull['head']['sha'] != candidate['head']:
        raise ValueError('in-flight PR changed; manual recovery is required')
    current = api(f'repos/{ORG}/{repository}/commits/main')
    if current['sha'] == candidate['base']:
        return
    if (current['commit']['tree']['sha'] != candidate['merge_tree']
            or [parent['sha'] for parent in current['parents']] != [candidate['base']]):
        raise ValueError('in-flight merge produced an unexpected main revision')
    # This exact case occurred after a GitHub 502: main was updated to the
    # approved squash tree but the PR was still OPEN. Retrying the exact head
    # completes the PR metadata without accepting a new source combination.
    merge(repository, candidate)


def advance(store):
    active = store.data['active']
    if not active or active['phase'] != 'running':
        return
    snapshot = active['snapshot']
    reconcile_inflight(active)
    applied = preflight(snapshot, active['actor'], active['force'])
    members = [name for name, candidate in snapshot['projects'].items() if 'pr' in candidate]
    # Publish manifest layout first; keep the coordinator revision stable until
    # all its dependencies are applied. GitHub has no cross-repository atomic merge.
    members.sort(key=lambda name: (2 if name == 'ci' else 0 if name == 'manifest' else 1, name))
    for repository in members:
        if repository in applied:
            continue
        # Recheck every remaining PR, review and dependency before each write.
        applied = preflight(snapshot, active['actor'], active['force'], {repository})
        if repository in applied:
            continue
        active['inflight'] = repository
        store.save()
        result = merge(repository, snapshot['projects'][repository])
        if not result.get('merged'):
            raise ValueError(f'{repository}: GitHub refused the merge: {result.get("message", "unknown")}')
        applied = verify_current(snapshot)
        if repository not in applied:
            raise ValueError(f'{repository}: merge is not yet confirmed; resume after GitHub settles')
        active['merged'] = sorted(applied)
        active['inflight'] = None
        store.save()
    verify_current(snapshot)
    archive = f'submissions/{snapshot["identity"]}.json'
    store.data['messages'][snapshot['target']] = 'Whole topic submitted successfully. Archive: ' + archive
    store.data['active'] = None
    store.save({archive: {**active, 'phase': 'completed'}})
