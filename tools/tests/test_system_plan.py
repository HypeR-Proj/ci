# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from system_plan import outcome, partition


class SystemPlanTests(unittest.TestCase):
    def plan(self):
        return {'cases': {'include': [
            {'id': 'vm', 'arch': 'riscv64', 'fixture': ''},
            {'id': 'boot', 'arch': 'riscv64', 'fixture': 'native'},
        ]}, 'fixtures': {'include': [{'name': 'native', 'arch': 'riscv64'}]}}

    def stages(self):
        stages = {name: {'result': 'success'} for name in
                  ('resolve', 'pending', 'plan', 'test', 'fixture-test', 'fixtures')}
        stages['plan']['outputs'] = {name: 'true' for name in
                                     ('has_standalone', 'has_consumers', 'has_fixtures')}
        return stages

    def test_independent_cases_do_not_wait_for_fixtures(self):
        result = partition(self.plan())
        self.assertEqual([case['id'] for case in result['standalone']['include']], ['vm'])
        self.assertEqual([case['id'] for case in result['consumers']['include']], ['boot'])
        workflow = (Path(__file__).resolve().parents[2] /
                    '.github/workflows/system.yml').read_text()
        standalone = workflow.split('\n  test:\n', 1)[1].split('\n  fixture-test:\n', 1)[0]
        self.assertIn('needs: [resolve, plan]', standalone)
        self.assertNotIn('needs.fixtures', standalone)

    def test_missing_or_wrong_arch_fixture_rejected(self):
        for fixtures in ([], [{'name': 'native', 'arch': 'aarch64'}]):
            plan = self.plan()
            plan['fixtures']['include'] = fixtures
            with self.assertRaises(ValueError):
                partition(plan)

    def test_empty_plan_rejected(self):
        plan = self.plan()
        plan['cases']['include'] = []
        with self.assertRaises(ValueError):
            partition(plan)

    def test_each_planned_lane_is_required(self):
        stages = self.stages()
        self.assertEqual(outcome(stages), 'success')
        for name in stages:
            for result in ('failure', 'cancelled', 'skipped'):
                changed = copy.deepcopy(stages)
                changed[name]['result'] = result
                self.assertEqual(outcome(changed), 'failure', (name, result))

    def test_optional_lanes_can_be_skipped(self):
        for only_consumers in (True, False):
            stages = self.stages()
            flags = stages['plan']['outputs']
            flags['has_standalone'] = str(not only_consumers).lower()
            flags['has_consumers'] = flags['has_fixtures'] = str(only_consumers).lower()
            for name in (['test'] if only_consumers else ['fixture-test', 'fixtures']):
                stages[name]['result'] = 'skipped'
            del stages['pending']  # Unprivileged preview has no pending-check job.
            self.assertEqual(outcome(stages), 'success')

    def test_missing_flags_or_no_cases_cannot_pass(self):
        for flags in ({}, {'has_standalone': 'false', 'has_consumers': 'false',
                           'has_fixtures': 'false'}):
            stages = self.stages()
            stages['plan']['outputs'] = flags
            self.assertEqual(outcome(stages), 'failure')


if __name__ == '__main__':
    unittest.main()
