#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Invalidate old green checks when their verified source combination changes."""

import argparse
import functools
import json
from pathlib import Path

import workspace
from configure import APP_ID
from gate import write_check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    workspace.api = functools.cache(workspace.api)
    checked = {}
    for name in workspace.project_names(args.manifest):
        for pull in workspace.open_pulls(name):
            checks = workspace.api(f"repos/{workspace.ORG}/{name}/commits/{pull['head']['sha']}/check-runs?check_name=system&filter=latest&per_page=100")['check_runs']
            for check in checks:
                if check['app']['id'] != APP_ID or check.get('conclusion') != 'success':
                    continue
                snapshot = json.loads(check['output']['text'])
                identity = snapshot['identity']
                if identity not in checked:
                    try:
                        workspace.verify_current(snapshot)
                        checked[identity] = None
                    except ValueError as error:
                        checked[identity] = str(error)
                if checked[identity]:
                    write_check(name, {'name': 'system', 'status': 'completed', 'conclusion': 'failure',
                                      'output': {'title': 'System verification is stale',
                                                 'summary': checked[identity], 'text': check['output']['text']}}, check['id'])


if __name__ == '__main__':
    main()
