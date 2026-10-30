#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Poll native PR buttons, coordinate whole-topic submission and retire labels."""

import argparse
import concurrent.futures
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from urllib.parse import quote

from configure import APP_ID
import refresh
from topic_requests import receive, target_for, permission, WORKFLOW
from topic_store import Store, pages, write
import topic_submit
from workspace import ORG, api, project_names, candidate_layout

CI = f'repos/{ORG}/ci'
RUNNING = {'queued', 'in_progress', 'requested', 'waiting', 'pending'}
BUTTONS = [
    {'label': 'Run system CI', 'description': 'Verify the complete current topic', 'identifier': 'verify'},
    {'label': 'Submit topic', 'description': 'Merge every verified topic member', 'identifier': 'submit'},
]


def inventory():
    # Archived repositories can still contain open PRs. They must not disappear
    # from membership or permit deletion of a still-used topic in another repo.
    repositories = list(pages(f'orgs/{ORG}/repos?type=all'))
    def read(repository):
        name = repository['name']
        return name, list(pages(f'repos/{ORG}/{name}/pulls?state=open'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        return dict(pool.map(read, repositories))


def members(pulls, target):
    if not target.startswith('topic:'):
        repository, number = target.rsplit('#', 1)
        return [(repository, pull) for pull in pulls.get(repository, []) if str(pull['number']) == number]
    return [(repository, pull) for repository, rows in pulls.items() for pull in rows
            if target.lower() in [label['name'].lower() for label in pull['labels']]]


def declared_repositories(manifest, pulls):
    known = set(project_names(manifest))
    # New repositories can join the same topic as their manifest registration.
    # This extends publication/validation only; the resolver still verifies the
    # exact selected manifest candidate before granting a system check.
    for pull in pulls.get('manifest', []):
        if pull['base']['ref'] == 'main':
            try:
                known.update(set(candidate_layout(pull['head']['sha'])) & pulls.keys())
            except ValueError as error:
                print(f'Ignoring invalid manifest declaration in PR {pull["number"]}: {error}', flush=True)
            except subprocess.CalledProcessError as error:
                if 'HTTP 404' not in (error.stderr or ''):
                    raise
                print(f'Manifest PR {pull["number"]} has no readable module declaration', flush=True)
    return known


def validate_members(pulls, target, known):
    selected = members(pulls, target)
    if not selected:
        raise ValueError('no open PRs remain for this target')
    seen = set()
    for repository, pull in selected:
        if repository not in known or repository in seen:
            raise ValueError('topic has an unsupported repository or multiple PRs in one repository')
        if pull['base']['ref'] != 'main' or target_for(repository, pull) != target:
            raise ValueError('all topic members must target main and belong to exactly this topic')
        seen.add(repository)
    return selected


def system_runs():
    return list(pages(CI + '/actions/workflows/system.yml/runs?event=workflow_dispatch', 'workflow_runs'))


def dispatch_verification(store, request, identifier):
    prefix = f'System verification: {request["target"]} ['
    runs = system_runs()
    exact = [run for run in runs if run['display_title'] == prefix + identifier + ']']
    active = [run for run in runs if run['status'] in RUNNING and run['display_title'].startswith(prefix)]
    if exact or active:
        run = (exact or active)[0]
        return 'System verification: ' + run['html_url']
    now = datetime.now(timezone.utc)
    for previous_id, previous in store.data['requests'].items():
        if (previous_id == identifier or not previous.get('dispatched_at')
                or previous.get('request', {}).get('target') != request['target']):
            continue
        visible = any(run['display_title'] == prefix + previous_id + ']' for run in runs)
        started = datetime.fromisoformat(previous['dispatched_at'])
        if not visible and now - started < timedelta(minutes=10):
            return 'A verification request for this topic is awaiting GitHub run registration.'
    # Record intent before POST. A lost response is reconciled by the unique
    # run title on the next poll, rather than immediately launching a duplicate.
    row = store.data['requests'][identifier]
    row.update(request=request, phase='dispatching', dispatched_at=now.isoformat())
    store.save()
    write(CI + '/actions/workflows/system.yml/dispatches', {
        'ref': 'main', 'inputs': {'target': request['target'], 'request_id': identifier}})
    return 'System verification requested. Its run link will appear on the system check.'


def process(store, manifest, pulls, known):
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d')
    def read(repository):
        return repository, list(pages(f'repos/{ORG}/{repository}/actions/runs'
            f'?event=check_run&status=completed&created={quote(">=" + since)}', 'workflow_runs'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        collections = list(pool.map(read, sorted(known)))
    queued = [(repository, run) for repository, runs in collections for run in runs
              if run['path'] == WORKFLOW and run['conclusion'] == 'success']
    for repository, run in sorted(queued, key=lambda pair: pair[1]['created_at']):
        identifier = f'{repository}-{run["id"]}'
        previous = store.data['requests'].get(identifier)
        if previous and previous['phase'] in ('done', 'rejected'):
            continue
        store.data['requests'].setdefault(identifier, {'phase': 'received', 'url': run['html_url']})
        request = None
        try:
            request = receive(repository, run)
            validate_members(pulls, request['target'], known)
            if request['action'] == 'verify':
                message = dispatch_verification(store, request, identifier)
            else:
                topic_submit.begin(store, manifest, request)
                message = 'Whole-topic submission accepted; progress is recorded in the coordinator.'
            store.data['requests'][identifier].update(phase='done', request=request)
            store.data['messages'][request['target']] = message
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            store.data['requests'][identifier].update(phase='rejected', error=str(error))
            if request:
                store.data['messages'][request['target']] = str(error)
            print(f'Rejected {identifier}: {error}', flush=True)
        store.save()


def publish_controls(store, pulls, known):
    for repository in sorted(known):
        for pull in pulls.get(repository, []):
            if pull['base']['ref'] != 'main':
                continue
            target = f'{repository}#{pull["number"]}'
            actions = []
            try:
                target = target_for(repository, pull)
                selected = validate_members(pulls, target, known)
                summary = 'Target: `' + target + '`\n\n' + '\n'.join(
                    f'- [{name}#{row["number"]}]({row["html_url"]})' for name, row in selected)
                if pull['draft']:
                    summary += '\n\nMark this PR ready before requesting verification or submission.'
                else:
                    actions = BUTTONS
            except ValueError as error:
                summary = str(error)
            controls_url = f'https://github.com/{ORG}/{repository}/pull/{pull["number"]}/checks'
            summary += '\n\nOpen the **topic** check in this PR\'s **Checks** tab to use '
            summary += '**Run system CI** or **Submit topic**. '
            summary += 'Buttons are processed by the central coordinator, normally within several minutes. '
            summary += 'Submission includes every topic member. Do not use the ordinary Merge button.'
            if target in store.data['messages']:
                summary += '\n\nLast request: ' + store.data['messages'][target]
            active = store.data['active']
            if active and active['snapshot']['target'] == target:
                summary += f'\n\nSubmission: {active["phase"]}; merged: {", ".join(active["merged"]) or "none"}.'
                if active['error']:
                    summary += '\n\n' + active['error']
            identity = f'topic:{repository}#{pull["number"]}'
            payload = {'name': 'topic', 'external_id': identity, 'status': 'completed',
                       'conclusion': 'neutral', 'actions': actions,
                       'details_url': controls_url,
                       'output': {'title': 'Topic controls', 'summary': summary,
                                  'text': json.dumps({'target': target, 'pr': pull['number'],
                                                      'buttons': actions})}}
            checks = list(pages(f'repos/{ORG}/{repository}/commits/{pull["head"]["sha"]}/check-runs'
                               '?check_name=topic&filter=all', 'check_runs'))
            own = [check for check in checks if check['app']['id'] == APP_ID and check.get('external_id') == identity]
            if own:
                check = max(own, key=lambda row: row['id'])
                payload['details_url'] += f'?check_run_id={check["id"]}'
                # GitHub does not consistently echo requested actions in GET
                # responses. Their version is also in output.text for comparison.
                changed = any(check.get(key) != value for key, value in payload.items()
                              if key not in ('actions', 'output'))
                changed |= any(check.get('output', {}).get(key) != value
                               for key, value in payload['output'].items())
                if changed:
                    write(f'repos/{ORG}/{repository}/check-runs/{check["id"]}', payload, 'PATCH')
            else:
                check = write(f'repos/{ORG}/{repository}/check-runs', {**payload, 'head_sha': pull['head']['sha']})
                write(f'repos/{ORG}/{repository}/check-runs/{check["id"]}',
                      {'details_url': controls_url + f'?check_run_id={check["id"]}'}, 'PATCH')


def clean_labels(store, pulls):
    if store.data['active'] or any(run['status'] in RUNNING for run in system_runs()):
        return
    live = {label['name'] for rows in pulls.values() for pull in rows for label in pull['labels']}
    by_topic = {}
    for repository in sorted(pulls):
        for label in pages(f'repos/{ORG}/{repository}/labels'):
            if label['name'].startswith('topic:'):
                by_topic.setdefault(label['name'], []).append(repository)
    for topic, repositories in by_topic.items():
        if topic in live:
            continue
        rows = []
        for repository in repositories:
            for item in pages(f'repos/{ORG}/{repository}/issues?state=all&labels={quote(topic, safe="")}'):
                rows.append({'repository': repository, 'number': item['number'], 'url': item['html_url'],
                             'state': item['state'], 'closed_at': item['closed_at'],
                             'title': item['title'], 'pull_request': 'pull_request' in item})
        # An unused label may have just been created for an upcoming topic.
        # Open issues also retain their labels even though they cannot be submitted.
        if not rows or any(row['state'] != 'closed' for row in rows):
            continue
        newest = max(datetime.fromisoformat(row['closed_at'].replace('Z', '+00:00')) for row in rows)
        if datetime.now(timezone.utc) - newest < timedelta(hours=24):
            continue
        archive = 'topics/' + hashlib.sha256(topic.encode()).hexdigest() + '.json'
        store.data['retired'][topic] = archive
        store.save({archive: {'topic': topic, 'members': rows,
                             'last_result': store.data['messages'].get(topic)}})
        # Recheck the whole organization's open membership before any deletion.
        if members(inventory(), topic):
            continue
        for repository in repositories:
            if list(pages(f'repos/{ORG}/{repository}/issues?state=open&labels={quote(topic, safe="")}')):
                break
            try:
                write(f'repos/{ORG}/{repository}/labels/{quote(topic, safe="")}', method='DELETE')
            except subprocess.CalledProcessError as error:
                if 'HTTP 404' not in (error.stderr or ''):
                    raise
        print(f'Archived closed topic {topic}: {archive}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--action', choices=['controls', 'poll', 'submit', 'force-submit'], default='poll')
    parser.add_argument('--target', default='')
    parser.add_argument('--actor', default='')
    args = parser.parse_args()
    pulls = inventory()
    known = declared_repositories(args.manifest, pulls)
    if known - pulls.keys():
        raise ValueError('manifest includes missing or inaccessible repositories')
    if args.action == 'controls':
        # Publication needs only PR reads and Check writes. It neither reads
        # submission state nor dispatches verification or merges a PR. Keep it
        # usable from the resolver without waiting for a scheduled poll.
        from types import SimpleNamespace
        publish_controls(SimpleNamespace(data={'messages': {}, 'active': None}), pulls, known)
        return
    store = Store()
    if args.action != 'poll':
        selected = validate_members(pulls, args.target, known)
        repository, pull = selected[0]
        permission(repository, args.actor)
        request = {'target': args.target, 'actor': args.actor, 'repository': repository,
                   'pr': pull['number'], 'head': pull['head']['sha']}
        topic_submit.begin(store, args.manifest, request, args.action == 'force-submit')
    else:
        process(store, args.manifest, pulls, known)
    try:
        topic_submit.advance(store)
    except ValueError as error:
        if store.data['active']:
            store.data['active'].update(phase='blocked', error=str(error))
            store.save()
        print('Submission blocked:', error, flush=True)
    # Do not invalidate checks halfway through a pending merge transaction.
    if not store.data['active']:
        refresh.refresh(args.manifest)
    pulls = inventory()
    publish_controls(store, pulls, known)
    clean_labels(store, pulls)


if __name__ == '__main__':
    main()
