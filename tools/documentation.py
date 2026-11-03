#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Select the exact documentation tools for a read-only PR site build."""

import os
from pathlib import Path

from workspace import ORG, SHA, api, open_pulls


def tools_revision(repository, number, candidate):
    if repository == 'docs':
        if not SHA.fullmatch(candidate):
            raise ValueError('documentation candidate must be an exact commit')
        return candidate
    if not number:
        return 'main'
    pull = api(f'repos/{ORG}/{repository}/pulls/{number}')
    topics = [label['name'] for label in pull['labels'] if label['name'].startswith('topic:')]
    if len(topics) > 1:
        raise ValueError('a documentation candidate may belong to only one topic')
    if not topics:
        return 'main'
    members = [pull for pull in open_pulls('docs')
               if topics[0] in [label['name'] for label in pull['labels']]]
    if len(members) > 1:
        raise ValueError('a topic may contain at most one documentation PR')
    if not members:
        return 'main'
    selected = api(f'repos/{ORG}/docs/pulls/{members[0]["number"]}')
    revision = selected.get('merge_commit_sha') or ''
    if (selected['state'] != 'open' or selected['base']['ref'] != 'main'
            or selected.get('mergeable') is not True or not SHA.fullmatch(revision)
            or topics[0] not in [label['name'] for label in selected['labels']]):
        raise ValueError('documentation topic candidate changed or has no merge commit; retry after resolving it')
    return revision


def main():
    revision = tools_revision(os.environ['REPOSITORY'], int(os.environ['PR_NUMBER']),
                              os.environ['CANDIDATE_SHA'])
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
        output.write(f'revision={revision}\n')
    print(f'Documentation tools: {revision}')


if __name__ == '__main__':
    main()
