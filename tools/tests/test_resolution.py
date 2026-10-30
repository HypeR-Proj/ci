# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

import copy
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workspace


class ResolutionTests(unittest.TestCase):
    def pull(self, head='b' * 40):
        return {'number': 1, 'state': 'OPEN', 'baseRefName': 'main',
                'headRefOid': head, 'mergeable': 'MERGEABLE',
                'potentialMergeCommit': {'oid': 'c' * 40, 'tree': {'oid': 'd' * 40}}}

    def repository(self, pulls=()):
        return {'defaultBranchRef': {'name': 'main', 'target': {'oid': 'a' * 40}},
                'members': {'nodes': list(pulls), 'pageInfo': {'hasNextPage': False}}}

    def test_large_topic_reads_all_heads_and_merge_trees_in_one_query(self):
        names = ['manifest', *[f'module-{i}' for i in range(64)]]
        data = {f'r{i}': self.repository([self.pull()]) for i in range(len(names))}
        with patch.object(workspace, 'graphql', return_value=data) as query, patch.object(workspace, 'api') as rest:
            result = workspace.read_candidates(names, 'topic:large')
        query.assert_called_once()
        rest.assert_not_called()
        self.assertEqual(len(result), 65)
        self.assertIn('first:2,states:OPEN,baseRefName:"main",labels:["topic:large"]', query.call_args.args[0])
        self.assertEqual(result['module-63']['pull']['potentialMergeCommit']['tree']['oid'], 'd' * 40)

    def test_duplicate_or_truncated_topic_fails_closed(self):
        for duplicate, more in ((True, False), (False, True)):
            row = self.repository([self.pull()] * (2 if duplicate else 1))
            row['members']['pageInfo']['hasNextPage'] = more
            with patch.object(workspace, 'graphql', return_value={'r0': row}), self.assertRaisesRegex(ValueError, 'at most one'):
                workspace.read_candidates(['kernel'], 'topic:duplicate')

    def test_closed_single_pr_and_changed_default_branch_are_rejected(self):
        row = self.repository()
        row['pr'] = {**self.pull(), 'state': 'CLOSED'}
        with patch.object(workspace, 'graphql', return_value={'r0': row}), self.assertRaisesRegex(ValueError, 'open PR'):
            workspace.read_candidates(['kernel'], 'kernel#1')
        row['defaultBranchRef']['name'] = 'other'
        with patch.object(workspace, 'graphql', return_value={'r0': row}), self.assertRaisesRegex(ValueError, 'default branch'):
            workspace.read_candidates(['kernel'], 'main')

    def test_manifest_expansion_uses_fresh_sources_and_binds_layout_to_merge(self):
        xml = '<manifest><project name="manifest"/><project name="kernel"/></manifest>'
        initial = {'manifest': {'base': 'a' * 40, 'pull': self.pull()},
                   'kernel': {'base': 'a' * 40, 'pull': self.pull()}}
        expanded = {**copy.deepcopy(initial), 'new-module': {'base': 'a' * 40, 'pull': self.pull()}}
        expanded['kernel']['pull']['headRefOid'] = 'e' * 40
        layout = {name: {'path': name, 'links': []} for name in expanded}
        with patch.object(workspace, 'read_candidates', side_effect=[initial, expanded]) as reads, patch.object(workspace, 'candidate_layout', return_value=layout) as metadata:
            snapshot = workspace.resolve(io.StringIO(xml), 'topic:expansion')
        self.assertEqual(reads.call_count, 2)
        metadata.assert_called_once_with('c' * 40)
        self.assertEqual(snapshot['projects']['kernel']['head'], 'e' * 40)
        self.assertEqual(snapshot['projects']['new-module']['merge_tree'], 'd' * 40)
        expanded['manifest']['base'] = 'f' * 40
        with patch.object(workspace, 'read_candidates', side_effect=[initial, expanded]), patch.object(workspace, 'candidate_layout', return_value=layout), self.assertRaisesRegex(ValueError, 'changed during resolution'):
            workspace.resolve(io.StringIO(xml), 'topic:expansion')

    def test_manifest_without_candidate_merge_cannot_supply_incomplete_layout(self):
        current = {'manifest': {'base': 'a' * 40, 'pull': {**self.pull(), 'potentialMergeCommit': None}}}
        with patch.object(workspace, 'read_candidates', return_value=current), self.assertRaisesRegex(ValueError, 'merge commit is unavailable'):
            workspace.resolve(io.StringIO('<manifest/>'), 'manifest#1')


if __name__ == '__main__':
    unittest.main()
