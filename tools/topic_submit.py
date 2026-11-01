#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Submit a verified topic in bounded, durably checkpointed batches."""

from collections import deque
import concurrent.futures
import json
import re
import time

from authorization import require_submitter, trusted_ownership
from configure import APP_ID
from topic_requests import permission, target_for
from topic_store import pages, write
from workspace import ORG, api, resolve, verify_current

ACTIONS_APP_ID = 15368
BATCH_SIZE = 8
MERGE_WORKERS = 4
# Leave headroom below GitHub's content-creation limit for admission, archival
# and other App activity. Each batch saves two checkpoints (three writes each).
WRITES_PER_MINUTE = 50


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


def latest_checks(repository, head):
    # Required gates are checked separately. Do not submit while an owner's
    # additional qualification (for example I/O VM kernel builds) is unfinished
    # or failing merely because it is not a named branch-protection requirement.
    latest = {}
    for check in pages(f'repos/{ORG}/{repository}/commits/{head}/check-runs?filter=all', 'check_runs'):
        key = (check['name'], check['app']['id'])
        if key not in latest or check['id'] > latest[key]['id']:
            latest[key] = check
    return latest


def remaining_checks_pass(repository, head, checks=None):
    checks = latest_checks(repository, head) if checks is None else checks
    for check in checks.values():
        if (check['status'] != 'completed'
                or check['conclusion'] not in ('success', 'neutral', 'skipped')):
            raise ValueError(f'{repository}: {check["name"]} has not completed successfully')


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


def preflight(snapshot, actor, force=False, repositories=None):
    require_submitter(actor)
    applied = verify_current(snapshot)
    def inspect(item):
        repository, candidate = item
        trusted_ownership(repository, candidate['base'])
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
            checks = latest_checks(repository, candidate['head'])
            for name, app in (('module', ACTIONS_APP_ID), ('system', APP_ID)):
                check = checks.get((name, app))
                if check is None:
                    raise ValueError(f'{repository}: {name} check is missing')
                successful(check, repository)
            if checks[('system', APP_ID)]['external_id'] != snapshot['identity']:
                raise ValueError(f'{repository}: system checks did not verify the same complete topic')
            remaining_checks_pass(repository, candidate['head'], checks)
    pending = [(name, candidate) for name, candidate in snapshot['projects'].items()
               if 'pr' in candidate and name not in applied
               and (repositories is None or name in repositories)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(inspect, pending))
    return applied


def begin(store, manifest, request, force=False):
    require_submitter(request.get('actor', ''))
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
    pending = active['inflight']
    if not pending:
        return
    # Resume checkpoints written by the previous one-PR-at-a-time coordinator.
    for repository in [pending] if isinstance(pending, str) else pending:
        reconcile_merge(repository, active['snapshot']['projects'][repository])


def reconcile_merge(repository, candidate):
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


def batches(members):
    # Layout must be visible before consumers; the running coordinator remains
    # on its trusted revision until all other repositories have been applied.
    if 'manifest' in members:
        yield ['manifest']
    ordinary = sorted(set(members) - {'manifest', 'ci'})
    for offset in range(0, len(ordinary), BATCH_SIZE):
        yield ordinary[offset:offset + BATCH_SIZE]
    if 'ci' in members:
        yield ['ci']


def pace_batch(recent, requests):
    """Bound this run's merge/checkpoint writes without delaying small topics."""
    while True:
        now = time.monotonic()
        while recent and recent[0][0] <= now - 60:
            recent.popleft()
        if sum(count for _, count in recent) + requests <= WRITES_PER_MINUTE:
            recent.append((now, requests))
            return
        time.sleep(max(0, recent[0][0] + 60 - now))


def merge_batch(snapshot, batch):
    errors = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MERGE_WORKERS) as pool:
        pending = {pool.submit(merge, name, snapshot['projects'][name]): name for name in batch}
        # All admitted requests settle before we verify or modify shared state.
        for future in concurrent.futures.as_completed(pending):
            name = pending[future]
            try:
                result = future.result()
                if not result.get('merged'):
                    errors[name] = result.get('message') or 'GitHub refused the merge'
            except Exception as error:
                errors[name] = (getattr(error, 'stderr', None) or str(error)).strip()
    return errors


def advance(store):
    active = store.data['active']
    if not active or active['phase'] != 'running':
        return
    snapshot = active['snapshot']
    reconcile_inflight(active)
    applied = preflight(snapshot, active['actor'], active['force'])
    members = [name for name, candidate in snapshot['projects'].items()
               if 'pr' in candidate and name not in applied]
    recent_writes = deque()
    for batch in batches(members):
        pace_batch(recent_writes, len(batch) + 6)
        # Check the whole source combination and this batch's current gates
        # after any rate-limit wait, then bind every merge to its tested head.
        applied = preflight(snapshot, active['actor'], active['force'], set(batch))
        batch = [name for name in batch if name not in applied]
        if not batch:
            continue
        active['inflight'] = batch
        store.save()
        print('Submitting batch: ' + ', '.join(batch), flush=True)
        errors = merge_batch(snapshot, batch)
        try:
            applied = verify_current(snapshot)
        except ValueError:
            # A lost response can leave main updated before PR metadata. Only
            # reconcile the saved batch, preserving exact tree/parent checks.
            reconcile_inflight(active)
            applied = verify_current(snapshot)
        active['merged'] = sorted(applied)
        missing = [name for name in batch if name not in applied]
        active['inflight'] = missing or None
        store.save()
        if missing:
            detail = '; '.join(f'{name}: {errors.get(name, "merge is not yet confirmed")}'
                               for name in missing)
            raise ValueError('Submission batch incomplete; resume the saved candidate. ' + detail)
        if errors:
            print('Confirmed merges despite failed replies: ' + ', '.join(sorted(errors)), flush=True)
        print(f'Confirmed {len(applied)} topic members', flush=True)
    applied = verify_current(snapshot)
    expected = {name for name, candidate in snapshot['projects'].items() if 'pr' in candidate}
    if applied != expected:
        raise ValueError('Whole-topic submission is not yet confirmed')
    active['merged'] = sorted(applied)
    active['inflight'] = None
    archive = f'submissions/{snapshot["identity"]}.json'
    store.data['messages'][snapshot['target']] = 'Whole topic submitted successfully. Archive: ' + archive
    store.data['active'] = None
    store.save({archive: {**active, 'phase': 'completed'}})
