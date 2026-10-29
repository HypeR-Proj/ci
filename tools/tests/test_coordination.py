# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import configure
import gate
import workspace


class CoordinationTests(unittest.TestCase):
    def test_administrator_cannot_push_or_rewrite_default_history(self):
        history, review = configure.rulesets()
        self.assertEqual(history['bypass_actors'], [])
        self.assertEqual({r['type'] for r in history['rules']}, {'deletion', 'non_fast_forward'})
        self.assertEqual(review['bypass_actors'], [{'actor_id': 5, 'actor_type': 'RepositoryRole', 'bypass_mode': 'pull_request'}])
        requirements = next(r for r in review['rules'] if r['type'] == 'required_status_checks')
        self.assertEqual({c['context'] for c in requirements['parameters']['required_status_checks']}, {'module', 'system'})

    def test_manifest_rejects_path_traversal_and_duplicates(self):
        for names in (['../outside'], ['kernel', 'kernel']):
            xml = '<manifest>' + ''.join(f'<project name="{name}"/>' for name in names) + '</manifest>'
            with self.subTest(names=names), self.assertRaises(ValueError):
                workspace.project_names(io.StringIO(xml))

    def test_direct_paths_and_hidden_manifest_are_supported(self):
        xml = '<manifest><project name="build" path="."/><project name="manifest" path=".workspace"/></manifest>'
        self.assertEqual(workspace.project_layout(io.StringIO(xml))['build']['path'], '.')
        self.assertEqual(workspace.project_names(io.StringIO(xml)), ['build', 'manifest'])
        for value in ('../escape', '/outside', '.git/objects', '.repo/manifests'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                workspace.project_layout(io.StringIO(f'<manifest><project name="x" path="{value}"/></manifest>'))

    def test_package_channels_are_resolved_once_and_bound_to_identity(self):
        snapshot = {'version': 1, 'target': 'main', 'projects': {}, 'identity': 'old'}
        with patch.object(workspace.subprocess, 'check_output', side_effect=['sha256:' + 'a' * 64]) as call:
            workspace.resolve_packages(snapshot)
        self.assertEqual(call.call_count, 1)
        self.assertTrue(snapshot['io_packages']['qemu'].endswith('@sha256:' + 'a' * 64))
        self.assertNotEqual(snapshot['identity'], 'old')
        with patch.object(workspace.subprocess, 'check_output', return_value='main'), self.assertRaises(ValueError):
            workspace.resolve_packages(snapshot)

    def test_topic_search_reads_all_pages(self):
        with patch.object(workspace, 'api', side_effect=[[{'number': n} for n in range(100)], [{'number': 101}]]) as call:
            self.assertEqual(len(list(workspace.open_pulls('kernel'))), 101)
            self.assertIn('page=2', call.call_args.args[0])

    def test_base_change_invalidates_tested_candidate(self):
        snapshot = {'target': 'kernel#1', 'projects': {'kernel': {'base': 'a' * 40, 'head': 'b' * 40, 'pr': 1}}}
        with patch.object(workspace, 'api', return_value={'commit': {'sha': 'c' * 40}}):
            with self.assertRaisesRegex(ValueError, 'stale'):
                workspace.verify_current(snapshot)

    def test_stale_candidate_cannot_publish_success(self):
        with patch.object(gate, 'verify_current', side_effect=ValueError('stale')), patch.object(gate, 'write_check') as write:
            with self.assertRaises(ValueError):
                gate.publish({}, 'success', 'https://github.com/example')
            write.assert_not_called()

    def test_verified_topic_member_may_be_merged_without_invalidating_peers(self):
        record = {'base': 'a' * 40, 'head': 'b' * 40, 'pr': 1, 'merge_tree': 'c' * 40}
        snapshot = {'target': 'topic:test', 'projects': {'kernel': record}}
        responses = [{'commit': {'sha': 'd' * 40}},
                     {'merged': True, 'head': {'sha': 'b' * 40}, 'merge_commit_sha': 'd' * 40},
                     {'tree': {'sha': 'c' * 40}}, []]
        with patch.object(workspace, 'api', side_effect=responses):
            self.assertEqual(workspace.verify_current(snapshot), {'kernel'})

    def test_merged_topic_member_with_different_contents_is_stale(self):
        record = {'base': 'a' * 40, 'head': 'b' * 40, 'pr': 1, 'merge_tree': 'c' * 40}
        snapshot = {'target': 'topic:test', 'projects': {'kernel': record}}
        responses = [{'commit': {'sha': 'd' * 40}},
                     {'merged': True, 'head': {'sha': 'b' * 40}, 'merge_commit_sha': 'd' * 40},
                     {'tree': {'sha': 'e' * 40}}]
        with patch.object(workspace, 'api', side_effect=responses), self.assertRaisesRegex(ValueError, 'stale'):
            workspace.verify_current(snapshot)

    def test_pending_is_not_a_success(self):
        snapshot = {'identity': 'test', 'projects': {'kernel': {'head': 'a' * 40, 'pr': 1}}}
        with patch.object(gate, 'write_check') as write:
            gate.publish(snapshot, 'pending', 'https://github.com/example')
            self.assertEqual(write.call_args.args[1]['status'], 'queued')
            self.assertNotIn('conclusion', write.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
