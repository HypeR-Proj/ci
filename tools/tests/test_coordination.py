# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

import io
import json
import os
import subprocess
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import configure
import gate
import refresh
import workspace


class CoordinationTests(unittest.TestCase):
    def candidate_state(self, main='a' * 40, head='b' * 40, tree='c' * 40,
                        merged=False, members=None):
        return {'r0': {'defaultBranchRef': {'name': 'main', 'target': {'oid': main, 'tree': {'oid': tree}}},
                       'pr': {'state': 'MERGED' if merged else 'OPEN', 'headRefOid': head,
                              'merged': merged, 'mergeCommit': {'oid': main, 'tree': {'oid': tree}} if merged else None},
                       'members': {'nodes': members if members is not None else [],
                                   'pageInfo': {'hasNextPage': False}}}}

    def test_truncated_read_response_is_retried(self):
        with patch.object(workspace.subprocess, 'check_output', side_effect=['{', '{"ok": true}']) as call, patch.object(workspace.time, 'sleep'):
            self.assertEqual(workspace.api('repos/example'), {'ok': True})
        self.assertEqual(call.call_count, 2)

    def check_payload(self):
        return {'name': 'system', 'head_sha': 'a' * 40, 'external_id': 'candidate',
                'details_url': 'https://github.com/run/1', 'status': 'queued'}

    def test_lost_create_response_does_not_duplicate_the_check(self):
        checks = []
        def create(*args, **kwargs):
            checks.append({'id': 42, 'app': {'id': gate.APP_ID},
                           'external_id': 'candidate', 'details_url': 'https://github.com/run/1',
                           'status': 'queued'})
            raise subprocess.CalledProcessError(1, ['gh'], stderr='unexpected end of JSON input')
        with patch.object(gate, 'api', side_effect=lambda _: {'check_runs': list(checks)}), patch.object(gate.subprocess, 'check_output', side_effect=create) as call, patch.object(workspace.time, 'sleep'):
            self.assertEqual(gate.write_check('kernel', self.check_payload()), 42)
        self.assertEqual(call.call_count, 1)

    def test_check_from_other_run_app_or_completed_attempt_is_not_reused(self):
        check = {'id': 42, 'app': {'id': gate.APP_ID}, 'external_id': 'candidate',
                 'details_url': 'https://github.com/run/1', 'status': 'queued'}
        checks = [{**check, 'app': {'id': 1}}, {**check, 'external_id': 'other'},
                  {**check, 'details_url': 'https://github.com/run/2'},
                  {**check, 'status': 'completed'}]
        with patch.object(gate, 'api', return_value={'check_runs': checks}), patch.object(gate.subprocess, 'check_output', return_value='{"id": 43}') as call:
            self.assertEqual(gate.write_check('kernel', self.check_payload()), 43)
        self.assertEqual(call.call_count, 1)

    def test_persistent_transient_failure_has_a_retry_limit(self):
        failure = subprocess.CalledProcessError(1, ['gh'], stderr='unexpected end of JSON input')
        with patch.object(gate, 'api', return_value={'check_runs': []}), patch.object(gate.subprocess, 'check_output', side_effect=failure) as call, patch.object(workspace.time, 'sleep'):
            with self.assertRaises(subprocess.CalledProcessError):
                gate.write_check('kernel', self.check_payload())
        self.assertEqual(call.call_count, 3)

    def test_permission_failure_is_not_retried(self):
        failure = subprocess.CalledProcessError(1, ['gh'], stderr='HTTP 403: Resource not accessible')
        with patch.object(gate, 'api', return_value={'check_runs': []}), patch.object(gate.subprocess, 'check_output', side_effect=failure) as call, patch.object(workspace.time, 'sleep') as sleep:
            with self.assertRaises(subprocess.CalledProcessError):
                gate.write_check('kernel', self.check_payload())
        self.assertEqual(call.call_count, 1)
        sleep.assert_not_called()

    def test_lost_update_response_retries_the_same_check(self):
        payload = {'status': 'completed', 'conclusion': 'success'}
        with patch.object(gate, 'api') as lookup, patch.object(gate.subprocess, 'check_output', side_effect=['', '{"id": 42}']) as call, patch.object(workspace.time, 'sleep'):
            self.assertEqual(gate.write_check('kernel', payload, 42), 42)
        lookup.assert_not_called()
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args_list[0], call.call_args_list[1])

    def test_administrator_cannot_push_or_rewrite_default_history(self):
        history, review, submission = configure.rulesets()
        self.assertEqual(history['bypass_actors'], [])
        self.assertEqual({r['type'] for r in history['rules']}, {'deletion', 'non_fast_forward'})
        self.assertEqual(review['bypass_actors'], [
            {'actor_id': 5, 'actor_type': 'RepositoryRole', 'bypass_mode': 'pull_request'},
            {'actor_id': configure.APP_ID, 'actor_type': 'Integration', 'bypass_mode': 'pull_request'}])
        self.assertEqual(submission['rules'], [{'type': 'update'}])
        self.assertEqual(submission['bypass_actors'], [
            {'actor_id': configure.APP_ID, 'actor_type': 'Integration', 'bypass_mode': 'pull_request'}])
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

    def test_checkout_uses_resolved_commit_when_the_pull_ref_advances(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / 'remote'
            remote.mkdir()
            def git(*arguments):
                return subprocess.check_output(['git', *arguments], cwd=remote, text=True).strip()
            git('init', '-q')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.invalid')
            revisions = []
            for value in ('base', 'selected', 'newer'):
                (remote / 'source').write_text(value)
                git('add', 'source')
                git('-c', 'commit.gpgsign=false', 'commit', '-qm', value)
                revisions.append(git('rev-parse', 'HEAD'))
            git('update-ref', 'refs/pull/1/head', revisions[2])
            record = {'base': revisions[0], 'head': revisions[1], 'pr': 1, 'path': 'kernel'}
            environment = {'GIT_CONFIG_COUNT': '1',
                           'GIT_CONFIG_KEY_0': 'url.' + str(remote) + '.insteadOf',
                           'GIT_CONFIG_VALUE_0': 'https://github.com/HypeR-Proj/kernel.git'}
            with patch.dict(os.environ, environment):
                workspace.checkout_one(root / 'workspace', 'kernel', record)
            self.assertEqual((root / 'workspace/kernel/source').read_text(), 'selected')
            snapshot = {'target': 'kernel#1', 'projects': {'kernel': record}}
            with patch.object(workspace, 'graphql', return_value=self.candidate_state(main=revisions[0], head=revisions[2])), patch.object(workspace.time, 'sleep'), self.assertRaisesRegex(ValueError, 'PR changed'):
                workspace.verify_current(snapshot)

    def test_topic_search_reads_all_pages(self):
        with patch.object(workspace, 'api', side_effect=[[{'number': n} for n in range(100)], [{'number': 101}]]) as call:
            self.assertEqual(len(list(workspace.open_pulls('kernel'))), 101)
            self.assertIn('page=2', call.call_args.args[0])

    def test_base_change_invalidates_tested_candidate(self):
        snapshot = {'target': 'kernel#1', 'projects': {'kernel': {'base': 'a' * 40, 'head': 'b' * 40, 'pr': 1}}}
        with patch.object(workspace, 'graphql', return_value=self.candidate_state(main='c' * 40)), patch.object(workspace.time, 'sleep'):
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
        with patch.object(workspace, 'graphql', return_value=self.candidate_state(main='d' * 40, merged=True)):
            self.assertEqual(workspace.verify_current(snapshot), {'kernel'})

    def test_merged_topic_member_with_different_contents_is_stale(self):
        record = {'base': 'a' * 40, 'head': 'b' * 40, 'pr': 1, 'merge_tree': 'c' * 40}
        snapshot = {'target': 'topic:test', 'projects': {'kernel': record}}
        with patch.object(workspace, 'graphql', return_value=self.candidate_state(main='d' * 40, tree='e' * 40, merged=True)), patch.object(workspace.time, 'sleep'), self.assertRaisesRegex(ValueError, 'stale'):
            workspace.verify_current(snapshot)

    def topic_snapshot(self):
        return {'identity': 'candidate', 'target': 'topic:test', 'projects': {
            'kernel': {'base': 'a' * 40, 'head': 'b' * 40, 'pr': 1, 'merge_tree': 'c' * 40}}}

    def test_unchanged_open_topic_remains_valid(self):
        state = self.candidate_state(members=[{'number': 1, 'headRefOid': 'b' * 40}])
        with patch.object(workspace, 'graphql', return_value=state) as read:
            self.assertEqual(workspace.verify_current(self.topic_snapshot()), set())
        self.assertEqual(read.call_count, 1)

    def test_changed_dependency_without_a_pr_is_stale(self):
        snapshot = {'target': 'main', 'projects': {'kernel': {'base': 'a' * 40}}}
        with patch.object(workspace, 'graphql', return_value=self.candidate_state(main='d' * 40)), patch.object(workspace.time, 'sleep'), self.assertRaisesRegex(ValueError, 'stale'):
            workspace.verify_current(snapshot)

    def test_merge_overlapping_a_read_is_confirmed_with_fresh_state(self):
        # The first response saw the old main but the PR had already left the
        # open-topic list. The second response observes the completed merge.
        partial = self.candidate_state()
        completed = self.candidate_state(main='d' * 40, merged=True)
        with patch.object(workspace, 'graphql', side_effect=[partial, completed]) as read, patch.object(workspace.time, 'sleep'):
            self.assertEqual(workspace.verify_current(self.topic_snapshot()), {'kernel'})
        self.assertEqual(read.call_count, 2)

    def test_real_topic_changes_are_not_accepted_after_a_retry(self):
        state = self.candidate_state(members=[{'number': 2, 'headRefOid': 'b' * 40}])
        with patch.object(workspace, 'graphql', return_value=state) as read, patch.object(workspace.time, 'sleep'), self.assertRaisesRegex(ValueError, 'topic membership'):
            workspace.verify_current(self.topic_snapshot())
        self.assertEqual(read.call_count, 2)

    def test_unexpected_additional_topic_member_is_stale(self):
        state = self.candidate_state(members=[{'number': 1, 'headRefOid': 'b' * 40},
                                             {'number': 2, 'headRefOid': 'e' * 40}])
        with patch.object(workspace, 'graphql', return_value=state), patch.object(workspace.time, 'sleep'), self.assertRaisesRegex(ValueError, 'topic membership'):
            workspace.verify_current(self.topic_snapshot())

    def test_topic_removal_is_stale(self):
        with patch.object(workspace, 'graphql', return_value=self.candidate_state()), patch.object(workspace.time, 'sleep'), self.assertRaisesRegex(ValueError, 'topic membership'):
            workspace.verify_current(self.topic_snapshot())

    def test_graphql_failure_does_not_invalidate_green_checks(self):
        with patch.object(workspace.subprocess, 'check_output', return_value=json.dumps({'errors': [{'message': 'unavailable'}], 'data': {}})), self.assertRaises(RuntimeError):
            workspace.verify_current(self.topic_snapshot())

    def test_unreadable_state_does_not_write_failure_over_success(self):
        pull = {'head': {'sha': 'b' * 40}, 'base': {'repo': {'name': 'kernel'}}}
        check = {'id': 42, 'app': {'id': refresh.APP_ID}, 'conclusion': 'success',
                 'output': {'text': json.dumps(self.topic_snapshot())}}
        with patch.object(workspace, 'project_names', return_value=['kernel']), patch.object(workspace, 'open_pulls', return_value=[pull]), patch.object(workspace, 'api', return_value={'check_runs': [check]}), patch.object(workspace, 'verify_current', side_effect=RuntimeError('unreadable')), patch.object(refresh, 'write_check') as write, self.assertRaises(RuntimeError):
            refresh.refresh(Path('unused.xml'))
        write.assert_not_called()

    def test_rename_uses_canonical_repository_for_check_updates(self):
        pull = {'head': {'sha': 'b' * 40}, 'base': {'repo': {'name': 'lib-system'}}}
        check = {'id': 42, 'app': {'id': refresh.APP_ID}, 'conclusion': 'success',
                 'output': {'text': json.dumps(self.topic_snapshot())}}
        with patch.object(workspace, 'project_names', return_value=['lib']), patch.object(workspace, 'open_pulls', return_value=[pull]), patch.object(workspace, 'api', return_value={'check_runs': [check]}) as read, patch.object(workspace, 'verify_current', side_effect=ValueError('stale')), patch.object(refresh, 'write_check') as write:
            refresh.refresh(Path('unused.xml'))
        self.assertIn('/lib-system/', read.call_args.args[0])
        self.assertEqual(write.call_args.args[0], 'lib-system')
        self.assertEqual(write.call_args.args[2], 42)

    def test_pending_is_not_a_success(self):
        snapshot = {'identity': 'test', 'projects': {'kernel': {'head': 'a' * 40, 'pr': 1}}}
        with patch.object(gate, 'write_check') as write:
            gate.publish(snapshot, 'pending', 'https://github.com/example')
            self.assertEqual(write.call_args.args[1]['status'], 'queued')
            self.assertNotIn('conclusion', write.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
