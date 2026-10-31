# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Separate fixture consumers from independent cases and check every planned lane."""

import json
import os
import sys


def partition(plan):
    cases = plan['cases']['include']
    if not cases:
        raise ValueError('system verification must contain cases')
    standalone = [case for case in cases if not case.get('fixture')]
    consumers = [case for case in cases if case.get('fixture')]
    fixtures = plan['fixtures']['include']
    available = {(item['name'], item['arch']) for item in fixtures}
    if any((case['fixture'], case['arch']) not in available for case in consumers):
        raise ValueError('case references an unplanned fixture')
    return {
        'standalone': {'include': standalone},
        'consumers': {'include': consumers},
        'fixtures': plan['fixtures'],
        'has_standalone': bool(standalone),
        'has_consumers': bool(consumers),
        'has_fixtures': bool(fixtures),
    }


def outcome(stages):
    required = ['resolve', 'plan']
    if 'pending' in stages:
        required.append('pending')
    flags = stages.get('plan', {}).get('outputs', {})
    lanes = {'has_standalone': 'test', 'has_consumers': 'fixture-test',
             'has_fixtures': 'fixtures'}
    if any(flags.get(flag) not in ('true', 'false') for flag in lanes):
        return 'failure'
    if flags['has_standalone'] == flags['has_consumers'] == 'false':
        return 'failure'
    if flags['has_consumers'] == 'true' and flags['has_fixtures'] != 'true':
        return 'failure'
    required.extend(lane for flag, lane in lanes.items() if flags[flag] == 'true')
    return ('success' if all(stages.get(name, {}).get('result') == 'success'
                             for name in required) else 'failure')


if __name__ == '__main__':
    if sys.argv[1] == 'matrix':
        with open(sys.argv[2]) as source:
            values = partition(json.load(source))
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            for name, value in values.items():
                print(name + '=' + json.dumps(value, separators=(',', ':')), file=output)
    elif sys.argv[1] == 'outcome':
        print(outcome(json.loads(os.environ['RESULTS'])))
    else:
        raise SystemExit('expected matrix or outcome')
