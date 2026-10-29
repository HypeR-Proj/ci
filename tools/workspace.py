#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Resolve and check out one explicit cross-repository verification candidate."""

import argparse
import base64
import concurrent.futures
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

ORG = 'HypeR-Proj'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def open_pulls(name):
    page = 1
    while True:
        rows = api(f'repos/{ORG}/{name}/pulls?state=open&base=main&per_page=100&page={page}')
        yield from rows
        if len(rows) < 100:
            break
        page += 1


def project_names(manifest):
    root = ET.parse(manifest).getroot()
    names = [p.attrib['name'] for p in root.findall('project')]
    if 'manifest' in names or len(names) != len(set(names)) or any(not NAME.fullmatch(n) for n in names):
        raise ValueError('manifest contains duplicate or invalid project names')
    return ['manifest', *names]


def resolve(manifest, target):
    names = project_names(manifest)
    selected = {}
    if target.startswith('topic:'):
        for name in names:
            prs = open_pulls(name)
            matches = [p for p in prs if target in [label['name'] for label in p['labels']]]
            if len(matches) > 1:
                raise ValueError(f'{name}: a topic may contain at most one PR per repository')
            if matches:
                selected[name] = matches[0]['number']
        if not selected:
            raise ValueError(f'no open PRs found for {target}')
    elif target and target != 'main':
        name, number = target.rsplit('#', 1)
        if name not in names or not number.isdecimal():
            raise ValueError('target must be main, repository#number, or topic:label')
        selected[name] = int(number)
    if 'manifest' in selected:
        pull = api(f"repos/{ORG}/manifest/pulls/{selected['manifest']}")
        content = api(f"repos/{ORG}/manifest/contents/default.xml?ref={pull['head']['sha']}")
        names = project_names(io.BytesIO(base64.b64decode(content['content'])))
        if target.startswith('topic:'):
            selected = {}
            for name in names:
                matches = [p for p in open_pulls(name) if target in [label['name'] for label in p['labels']]]
                if len(matches) > 1:
                    raise ValueError(f'{name}: a topic may contain at most one PR per repository')
                if matches:
                    selected[name] = matches[0]['number']
    projects = {}
    for name in names:
        branch = api(f'repos/{ORG}/{name}/branches/main')
        record = {'base': branch['commit']['sha']}
        if name in selected:
            pr = api(f'repos/{ORG}/{name}/pulls/{selected[name]}')
            if pr['state'] != 'open' or pr['base']['ref'] != 'main':
                raise ValueError(f'{name}: candidate must be an open PR against main')
            record.update(pr=pr['number'], head=pr['head']['sha'], draft=pr['draft'])
            if pr.get('merge_commit_sha') and pr.get('mergeable'):
                record['merge_tree'] = api(f"repos/{ORG}/{name}/git/commits/{pr['merge_commit_sha']}")['tree']['sha']
        projects[name] = record
    snapshot = {'version': 1, 'target': target, 'projects': projects}
    snapshot['identity'] = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return snapshot


def checkout_one(workspace, name, record):
    if not NAME.fullmatch(name) or not SHA.fullmatch(record['base']):
        raise ValueError('invalid source identity')
    root = workspace / name
    if root.exists():
        raise ValueError(f'refusing to overwrite existing checkout: {root}')
    subprocess.run(['git', 'clone', '--quiet', '--filter=blob:none', '--no-checkout',
                    f'https://github.com/{ORG}/{name}.git', str(root)], check=True)
    subprocess.run(['git', 'checkout', '--quiet', '--detach', record['base']], cwd=root, check=True)
    if 'pr' in record:
        if not isinstance(record['pr'], int) or not SHA.fullmatch(record['head']):
            raise ValueError('invalid PR identity')
        subprocess.run(['git', 'fetch', '--quiet', 'origin', f"refs/pull/{record['pr']}/head"], cwd=root, check=True)
        fetched = subprocess.check_output(['git', 'rev-parse', 'FETCH_HEAD'], cwd=root, text=True).strip()
        if fetched != record['head']:
            raise ValueError(f'{name}: PR changed during checkout; rerun verification')
        timestamp = max(int(subprocess.check_output(['git', 'show', '-s', '--format=%ct', revision], cwd=root))
                        for revision in (record['base'], fetched))
        environment = {**os.environ, 'GIT_AUTHOR_DATE': f'@{timestamp} +0000',
                       'GIT_COMMITTER_DATE': f'@{timestamp} +0000'}
        subprocess.run(['git', '-c', 'user.name=HypeR CI', '-c', 'user.email=ci@hyper.invalid',
                        '-c', 'commit.gpgsign=false', 'merge', '--quiet', '--no-ff',
                        '-m', f"Verify {record['base']} + {fetched}", fetched],
                       cwd=root, env=environment, check=True)
        tree = subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=root, text=True).strip()
        if record.get('merge_tree', tree) != tree:
            raise ValueError(f'{name}: resolved PR merge tree changed; rerun verification')


def checkout(snapshot, workspace):
    workspace.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(checkout_one, workspace, name, record)
                   for name, record in snapshot['projects'].items()]
        for future in futures:
            future.result()


def verify_current(snapshot):
    applied = set()
    for name, record in snapshot['projects'].items():
        head = api(f'repos/{ORG}/{name}/branches/main')['commit']['sha']
        if head != record['base']:
            if 'pr' in record and 'merge_tree' in record:
                pull = api(f"repos/{ORG}/{name}/pulls/{record['pr']}")
                if (pull.get('merged') and pull['head']['sha'] == record['head']
                        and pull.get('merge_commit_sha') == head
                        and api(f'repos/{ORG}/{name}/git/commits/{head}')['tree']['sha'] == record['merge_tree']):
                    applied.add(name)
                    continue
            raise ValueError(f'{name}: main changed; candidate verification is stale')
        if 'pr' in record:
            pr = api(f"repos/{ORG}/{name}/pulls/{record['pr']}")
            if pr['head']['sha'] != record['head'] or pr['state'] != 'open' or pr['draft']:
                raise ValueError(f'{name}: PR changed, closed, or is still a draft')
            if snapshot['target'].startswith('topic:') and snapshot['target'] not in [label['name'] for label in pr['labels']]:
                raise ValueError(f'{name}: topic membership changed')
    # Re-resolve to catch newly added topic members, including unchanged PR heads.
    if snapshot['target'].startswith('topic:'):
        members = {}
        for name in snapshot['projects']:
            for pr in open_pulls(name):
                if snapshot['target'] in [label['name'] for label in pr['labels']]:
                    if name in members:
                        raise ValueError(f'{name}: topic contains multiple open PRs')
                    members[name] = pr['head']['sha']
        expected = {name: record['head'] for name, record in snapshot['projects'].items() if 'pr' in record and name not in applied}
        if members != expected:
            raise ValueError('topic membership changed; rerun verification')
    return applied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    select = commands.add_parser('resolve')
    select.add_argument('--manifest', type=Path, required=True)
    select.add_argument('--target', default='main')
    select.add_argument('--output', type=Path, required=True)
    fetch = commands.add_parser('checkout')
    fetch.add_argument('--snapshot', type=Path, required=True)
    fetch.add_argument('--directory', type=Path, required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('--snapshot', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'resolve':
        args.output.write_text(json.dumps(resolve(args.manifest, args.target), indent=2) + '\n')
    elif args.command == 'checkout':
        checkout(json.loads(args.snapshot.read_text()), args.directory)
    else:
        verify_current(json.loads(args.snapshot.read_text()))


if __name__ == '__main__':
    main()
