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
import time
import xml.etree.ElementTree as ET

ORG = 'HypeR-Proj'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*\Z')
SHA = re.compile(r'[0-9a-f]{40}\Z')


def retry_github(operation):
    """Retry transient transport failures; writes must reconcile before retrying."""
    for attempt in range(3):
        try:
            return operation()
        except (json.JSONDecodeError, subprocess.CalledProcessError) as error:
            message = (getattr(error, 'stderr', None) or '').lower()
            transient = (isinstance(error, json.JSONDecodeError)
                         or re.search(r'http 5\d\d\b', message)
                         or any(marker in message for marker in (
                             'unexpected end of json input', 'unexpected eof',
                             'connection reset', 'tls handshake timeout')))
            if not transient or attempt == 2:
                if message:
                    error.add_note(message)
                raise
            time.sleep(2 ** attempt)


def api(path):
    return retry_github(lambda: json.loads(subprocess.check_output(
        ['gh', 'api', path], text=True, stderr=subprocess.PIPE)))


def open_pulls(name):
    page = 1
    while True:
        rows = api(f'repos/{ORG}/{name}/pulls?state=open&base=main&per_page=100&page={page}')
        yield from rows
        if len(rows) < 100:
            break
        page += 1


def safe_path(value):
    path = Path(value)
    if (not value or path.is_absolute() or '..' in path.parts
            or any(part in ('.git', '.repo') for part in path.parts)):
        raise ValueError(f'invalid manifest path: {value!r}')
    return path


def project_layout(manifest, metadata=None):
    root = ET.parse(manifest).getroot()
    result = {}
    paths = set()
    for item in root.findall('project'):
        name = item.attrib['name']
        path = str(safe_path(item.get('path', name)))
        if not NAME.fullmatch(name) or name in result or path in paths:
            raise ValueError('manifest contains duplicate or invalid projects')
        paths.add(path)
        links = []
        for kind in ('linkfile', 'copyfile'):
            for link in item.findall(kind):
                links.append({'kind': kind, 'src': str(safe_path(link.attrib['src'])),
                              'dest': str(safe_path(link.attrib['dest']))})
        result[name] = {'path': path, 'links': links}
    # Compatibility while the manifest migration PR is not yet merged.
    if 'manifest' not in result:
        result = {'manifest': {'path': 'manifest', 'links': []}, **result}
    if metadata is None and isinstance(manifest, (str, Path)):
        description = Path(manifest).resolve().parent / 'component.json'
        metadata = json.loads(description.read_bytes()) if description.exists() else {}
    for name in (metadata or {}).get('integration', {}).get('repositories', []):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError('invalid integration repository')
        if name not in result:
            path = '.integration/' + name
            if path in paths:
                raise ValueError('integration checkout conflicts with a source project')
            paths.add(path)
            result[name] = {'path': path, 'links': [], 'integration_only': True}
    return result


def candidate_layout(revision):
    if not SHA.fullmatch(revision):
        raise ValueError('manifest revision must be an exact commit')
    content = api(f'repos/{ORG}/manifest/contents/default.xml?ref={revision}')
    metadata = api(f'repos/{ORG}/manifest/contents/component.json?ref={revision}')
    return project_layout(io.BytesIO(base64.b64decode(content['content'])),
                          json.loads(base64.b64decode(metadata['content'])))


def project_names(manifest):
    return list(project_layout(manifest))


def resolve(manifest, target):
    layout = project_layout(manifest)
    names = list(layout)
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
        manifest_head = pull['head']['sha']
        manifest_revision = pull.get('merge_commit_sha') or manifest_head
        layout = candidate_layout(manifest_revision)
        names = list(layout)
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
        record = {'base': branch['commit']['sha'], **layout[name]}
        if name in selected:
            pr = api(f'repos/{ORG}/{name}/pulls/{selected[name]}')
            if pr['state'] != 'open' or pr['base']['ref'] != 'main':
                raise ValueError(f'{name}: candidate must be an open PR against main')
            if name == 'manifest' and pr['head']['sha'] != manifest_head:
                raise ValueError('manifest PR changed during resolution; rerun verification')
            record.update(pr=pr['number'], head=pr['head']['sha'])
            if pr.get('merge_commit_sha') and pr.get('mergeable'):
                record['merge_tree'] = api(f"repos/{ORG}/{name}/git/commits/{pr['merge_commit_sha']}")['tree']['sha']
        projects[name] = record
    snapshot = {'version': 1, 'target': target, 'projects': projects}
    snapshot['identity'] = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return snapshot


def checkout_one(workspace, name, record, candidates=None):
    if not NAME.fullmatch(name) or not SHA.fullmatch(record['base']):
        raise ValueError('invalid source identity')
    root = workspace / safe_path(record.get('path', name))
    if root.exists() and any(root.iterdir()):
        raise ValueError(f'refusing to overwrite existing checkout: {root}')
    if candidates and name in candidates:
        subprocess.run(['git', 'clone', '--quiet', '--no-local', str(candidates[name]), str(root)], check=True)
        return
    subprocess.run(['git', 'clone', '--quiet', '--filter=blob:none', '--no-checkout',
                    f'https://github.com/{ORG}/{name}.git', str(root)], check=True)
    subprocess.run(['git', 'checkout', '--quiet', '--detach', record['base']], cwd=root, check=True)
    if 'pr' in record:
        if not isinstance(record['pr'], int) or not SHA.fullmatch(record['head']):
            raise ValueError('invalid PR identity')
        subprocess.run(['git', 'fetch', '--quiet', 'origin', record['head']], cwd=root, check=True)
        fetched = subprocess.check_output(['git', 'rev-parse', 'FETCH_HEAD'], cwd=root, text=True).strip()
        if fetched != record['head']:
            raise ValueError(f'{name}: fetched object differs from the resolved candidate')
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


def checkout(snapshot, workspace, candidates=None):
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    projects = snapshot['projects']
    # Parents must be checked out before children. Clone siblings concurrently.
    depths = sorted({len(safe_path(r.get('path', n)).parts) for n, r in projects.items()})
    for depth in depths:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(checkout_one, workspace, name, record, candidates)
                       for name, record in projects.items()
                       if len(safe_path(record.get('path', name)).parts) == depth]
            for future in futures:
                future.result()
    destinations = set()
    for name, record in projects.items():
        root = workspace / safe_path(record.get('path', name))
        for link in record.get('links', []):
            source = root / safe_path(link['src'])
            destination = workspace / safe_path(link['dest'])
            if (destination in destinations or destination.exists() or destination.is_symlink()
                    or not destination.resolve().is_relative_to(workspace)):
                raise ValueError(f'manifest link would overwrite a source: {destination}')
            if not source.exists() or not source.resolve().is_relative_to(root.resolve()):
                raise ValueError(f'invalid manifest link source: {source}')
            destinations.add(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if link['kind'] == 'linkfile':
                destination.symlink_to(os.path.relpath(source, destination.parent))
            elif link['kind'] == 'copyfile' and source.is_file():
                import shutil
                shutil.copyfile(source, destination)
            else:
                raise ValueError('invalid manifest file operation')


def graphql(query):
    response = retry_github(lambda: json.loads(subprocess.check_output(
        ['gh', 'api', 'graphql', '--input', '-'],
        input=json.dumps({'query': query}), text=True, stderr=subprocess.PIPE)))
    if response.get('errors'):
        raise RuntimeError(f"GitHub could not read candidate state: {response['errors']}")
    return response['data']


def candidate_state_query(snapshot):
    fields = []
    topic = snapshot['target'].startswith('topic:')
    for index, (name, record) in enumerate(snapshot['projects'].items()):
        pull = (f"pr:pullRequest(number:{int(record['pr'])})"
                '{state headRefOid merged mergeCommit{oid tree{oid}}}'
                if 'pr' in record else '')
        members = ('members:pullRequests(first:2,states:OPEN,baseRefName:"main",labels:['
                   + json.dumps(snapshot['target'])
                   + ']){nodes{number headRefOid}pageInfo{hasNextPage}}' if topic else '')
        fields.append(f'r{index}:repository(owner:{json.dumps(ORG)},name:{json.dumps(name)})'
                      '{defaultBranchRef{name target{oid ... on Commit{tree{oid}}}}'
                      + pull + members + '}')
    return 'query {' + ' '.join(fields) + '}'


def verify_candidate_state(snapshot, data):
    applied = set()
    for index, (name, record) in enumerate(snapshot['projects'].items()):
        current = data[f'r{index}']
        if not current or not current['defaultBranchRef']:
            raise ValueError(f'{name}: default branch is missing; verification is stale')
        branch = current['defaultBranchRef']
        head = branch['target']
        if branch['name'] != 'main':
            raise ValueError(f'{name}: default branch changed; verification is stale')
        expected = []
        if 'pr' in record:
            pull = current['pr']
            if head['oid'] == record['base']:
                if not pull or pull['state'] != 'OPEN' or pull['headRefOid'] != record['head']:
                    raise ValueError(f'{name}: PR changed or closed; verification is stale')
                expected = [{'number': record['pr'], 'headRefOid': record['head']}]
            elif (pull and pull['merged'] and pull['headRefOid'] == record['head']
                  and pull['mergeCommit'] and pull['mergeCommit']['oid'] == head['oid']
                  and head['tree']['oid'] == record.get('merge_tree')):
                applied.add(name)
            else:
                raise ValueError(f'{name}: main changed; candidate verification is stale')
        elif head['oid'] != record['base']:
            raise ValueError(f'{name}: main changed; candidate verification is stale')
        if snapshot['target'].startswith('topic:'):
            members = current['members']
            if members['pageInfo']['hasNextPage'] or members['nodes'] != expected:
                raise ValueError(f'{name}: topic membership changed; verification is stale')
    return applied


def verify_current(snapshot):
    # Read heads, PR states and topic membership together. A cache spanning
    # separate REST requests can mix states from before and after a topic merge.
    query = candidate_state_query(snapshot)
    for attempt in range(2):
        data = graphql(query)
        try:
            return verify_candidate_state(snapshot, data)
        except ValueError:
            if attempt:
                raise
            # A merge can still overlap one query. Confirm a rejection using
            # a fresh response; genuine input changes continue to fail closed.
            time.sleep(1)


def resolve_packages(snapshot, oras='oras'):
    packages = {}
    registry = 'ghcr.io/hyper-proj/io-vm'
    for platform in ('qemu',):
        channel = registry + ':main-aarch64-' + platform
        digest = subprocess.check_output([oras, 'resolve', channel], text=True).strip()
        if re.fullmatch(r'sha256:[0-9a-f]{64}', digest) is None:
            raise ValueError('registry did not return an immutable package digest')
        packages[platform] = registry + '@' + digest
    snapshot['io_packages'] = packages
    snapshot.pop('identity', None)
    snapshot['identity'] = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    select = commands.add_parser('resolve')
    select.add_argument('--manifest', type=Path, required=True)
    select.add_argument('--target', default='main')
    select.add_argument('--packages', action='store_true')
    select.add_argument('--output', type=Path, required=True)
    fetch = commands.add_parser('checkout')
    fetch.add_argument('--snapshot', type=Path, required=True)
    fetch.add_argument('--directory', type=Path, required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('--snapshot', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'resolve':
        snapshot = resolve(args.manifest, args.target)
        if args.packages:
            resolve_packages(snapshot)
        args.output.write_text(json.dumps(snapshot, indent=2) + '\n')
    elif args.command == 'checkout':
        checkout(json.loads(args.snapshot.read_text()), args.directory)
    else:
        verify_current(json.loads(args.snapshot.read_text()))


if __name__ == '__main__':
    main()
