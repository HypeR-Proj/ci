# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from configure import APP_ID
import topic_requests as requests
import topic_store
import topic_submit as submit
import topics


def pull(number=1, topic='topic:test'):
    return {'number': number, 'state': 'open', 'draft': False, 'mergeable': True,
            'base': {'ref': 'main'}, 'head': {'sha': 'b' * 40},
            'labels': [{'name': topic}] if topic else [], 'html_url': 'https://example.invalid/pr'}


def snapshot():
    return {'target': 'topic:test', 'identity': 'test-identity', 'projects': {
        name: {'base': 'a' * 40, 'head': 'b' * 40, 'pr': 1, 'merge_tree': 'c' * 40}
        for name in ('kernel', 'ci')}}


def store():
    result = Mock()
    result.data = {'version': 1, 'active': None, 'messages': {}, 'requests': {}, 'retired': {}}
    return result


class ButtonTests(unittest.TestCase):
    def setUp(self):
        self.run = {'event': 'check_run', 'run_attempt': 1, 'status': 'completed',
                    'conclusion': 'success', 'path': requests.WORKFLOW,
                    'actor': {'id': 3, 'login': 'alice'}, 'triggering_actor': {'id': 3},
                    'html_url': 'https://example.invalid/run'}
        self.check = {'id': 42, 'app': {'id': APP_ID}, 'name': 'topic', 'external_id': 'topic:kernel#1',
                      'head_sha': 'b' * 40, 'output': {'text': json.dumps({'target': 'topic:test'})}}
        self.event = {'repository': {'full_name': 'HypeR-Proj/kernel'}, 'sender': {'id': 3},
                      'check_run': copy.deepcopy(self.check), 'action': 'requested_action',
                      'requested_action': {'identifier': 'submit'}}

    def test_native_button_and_rerequest(self):
        result = requests.validate_event('kernel', self.run, self.event, self.check, pull())
        self.assertEqual(result['action'], 'submit')
        self.event['action'] = 'rerequested'
        self.assertEqual(requests.validate_event('kernel', self.run, self.event, self.check, pull())['action'], 'verify')

    def test_forged_workflow_actor_app_or_head_is_rejected(self):
        for target, key, value in [
            ('run', 'event', 'pull_request'), ('run', 'path', '.github/workflows/evil.yml'),
            ('run', 'run_attempt', 2), ('run', 'triggering_actor', {'id': 4}),
            ('event', 'sender', {'id': 4}), ('check', 'app', {'id': 1}),
            ('check', 'head_sha', 'd' * 40), ('check', 'external_id', 'topic:kernel#2'),
        ]:
            run, event, check = copy.deepcopy((self.run, self.event, self.check))
            {'run': run, 'event': event, 'check': check}[target][key] = value
            with self.subTest(target=target, key=key), self.assertRaises(ValueError):
                requests.validate_event('kernel', run, event, check, pull())

    def test_force_action_and_stale_topic_are_rejected(self):
        self.event['requested_action']['identifier'] = 'force-submit'
        with self.assertRaises(ValueError):
            requests.validate_event('kernel', self.run, self.event, self.check, pull())
        self.event['requested_action']['identifier'] = 'submit'
        with self.assertRaisesRegex(ValueError, 'topic changed'):
            requests.validate_event('kernel', self.run, self.event, self.check, pull(topic='topic:new'))

    def test_ambiguous_and_mixed_case_labels_are_rejected(self):
        item = pull()
        item['labels'].append({'name': 'topic:other'})
        with self.assertRaises(ValueError):
            requests.target_for('kernel', item)
        with self.assertRaises(ValueError):
            requests.target_for('kernel', pull(topic='topic:Test'))
        with self.assertRaises(ValueError):
            requests.target_for('kernel', pull(topic='Topic:test'))
        with self.assertRaises(ValueError):
            topics.validate_members({'kernel': [pull()], 'ci': [pull(topic='Topic:test')]},
                                    'topic:test', {'kernel', 'ci'})

    def test_unknown_repository_cannot_be_silently_omitted(self):
        with self.assertRaises(ValueError):
            topics.validate_members({'kernel': [pull()], 'outside': [pull()]}, 'topic:test', {'kernel'})

    def test_single_pr_target_cannot_bypass_topic(self):
        with self.assertRaises(ValueError):
            topics.validate_members({'kernel': [pull()]}, 'kernel#1', {'kernel'})
        self.assertEqual(len(topics.validate_members({'kernel': [pull(topic='')]}, 'kernel#1', {'kernel'})), 1)


class SubmissionTests(unittest.TestCase):
    def test_no_merge_until_every_member_passes(self):
        state = store()
        request = {'repository': 'kernel', 'head': 'b' * 40, 'target': 'topic:test', 'actor': 'alice'}
        with patch.object(submit, 'verified_snapshot', return_value=snapshot()), \
                patch.object(submit, 'preflight', side_effect=ValueError('ci: module failed')), \
                patch.object(submit, 'merge') as merge:
            with self.assertRaises(ValueError):
                submit.begin(state, Path('unused'), request)
        self.assertIsNone(state.data['active'])
        merge.assert_not_called()

    def test_force_requires_admin_for_every_repository(self):
        with patch.object(submit, 'verify_current', return_value=set()), \
                patch.object(submit, 'permission', return_value=False):
            with self.assertRaisesRegex(ValueError, 'administrator'):
                submit.preflight(snapshot(), 'alice', force=True)

    def test_latest_failure_is_not_hidden_by_older_success(self):
        checks = [dict(id=i, name='module', app={'id': submit.ACTIONS_APP_ID},
                       status='completed', conclusion=outcome) for i, outcome in [(1, 'success'), (2, 'failure')]]
        with patch.object(submit, 'pages', return_value=checks):
            check = submit.latest_check('kernel', 'b' * 40, 'module', submit.ACTIONS_APP_ID)
        with self.assertRaises(ValueError):
            submit.successful(check, 'kernel')

    def test_partial_submission_resumes_frozen_snapshot_and_archives(self):
        state = store()
        state.data['active'] = {'snapshot': snapshot(), 'actor': 'alice', 'force': False,
                                'phase': 'running', 'inflight': None, 'error': None, 'merged': ['kernel']}
        with patch.object(submit, 'preflight', return_value={'kernel'}), \
                patch.object(submit, 'verify_current', return_value={'kernel', 'ci'}), \
                patch.object(submit, 'merge', return_value={'merged': True}) as merge:
            submit.advance(state)
        self.assertEqual(merge.call_count, 1)
        self.assertEqual(merge.call_args.args[0], 'ci')
        self.assertIsNone(state.data['active'])
        self.assertIn('submissions/test-identity.json', state.save.call_args.args[0])

    def test_additional_qualification_cannot_be_pending_failed_or_cancelled(self):
        for status, conclusion in [('in_progress', None), ('completed', 'failure'),
                                   ('completed', 'cancelled')]:
            check = dict(id=2, name='I/O VM qualification', app={'id': submit.ACTIONS_APP_ID},
                         status=status, conclusion=conclusion)
            with self.subTest(conclusion=conclusion), \
                    patch.object(submit, 'pages', return_value=[check]), self.assertRaises(ValueError):
                submit.remaining_checks_pass('io-vm', 'b' * 40)

    def test_additional_check_rerun_replaces_failure_but_not_another_apps_result(self):
        checks = [dict(id=i, name='qualification', app={'id': submit.ACTIONS_APP_ID},
                       status='completed', conclusion=result)
                  for i, result in [(1, 'failure'), (2, 'success')]]
        with patch.object(submit, 'pages', return_value=checks):
            submit.remaining_checks_pass('io-vm', 'b' * 40)
            checks.append({**checks[0], 'app': {'id': 42}})
            with self.assertRaises(ValueError):
                submit.remaining_checks_pass('io-vm', 'b' * 40)

    def test_merge_response_loss_keeps_inflight_checkpoint(self):
        state = store()
        state.data['active'] = {'snapshot': snapshot(), 'actor': 'alice', 'force': False,
                                'phase': 'running', 'inflight': None, 'error': None, 'merged': []}
        with patch.object(submit, 'preflight', return_value=set()), \
                patch.object(submit, 'merge', side_effect=subprocess.CalledProcessError(1, ['gh'])):
            with self.assertRaises(subprocess.CalledProcessError):
                submit.advance(state)
        self.assertEqual(state.data['active']['inflight'], 'kernel')
        state.save.assert_called_once()

    def test_changed_source_stops_before_next_merge(self):
        state = store()
        state.data['active'] = {'snapshot': snapshot(), 'actor': 'alice', 'force': False,
                                'phase': 'running', 'inflight': None, 'error': None, 'merged': []}
        with patch.object(submit, 'preflight', side_effect=[set(), ValueError('head changed')]), \
                patch.object(submit, 'merge') as merge:
            with self.assertRaisesRegex(ValueError, 'head changed'):
                submit.advance(state)
        merge.assert_not_called()

    def test_partial_github_merge_metadata_is_recovered_only_for_exact_tree(self):
        active = {'snapshot': snapshot(), 'inflight': 'kernel'}
        head = {'sha': 'd' * 40, 'commit': {'tree': {'sha': 'c' * 40}}, 'parents': [{'sha': 'a' * 40}]}
        pr = {**pull(), 'merged': False}
        with patch.object(submit, 'api', side_effect=[pr, head]), patch.object(submit, 'merge') as merge:
            submit.reconcile_inflight(active)
        merge.assert_called_once()
        head['commit']['tree']['sha'] = 'e' * 40
        with patch.object(submit, 'api', side_effect=[pr, head]), patch.object(submit, 'merge') as merge:
            with self.assertRaises(ValueError):
                submit.reconcile_inflight(active)
        merge.assert_not_called()

    def test_unfinished_other_topic_blocks_new_submission(self):
        state = store()
        state.data['active'] = {'snapshot': snapshot()}
        with self.assertRaisesRegex(ValueError, 'another topic'):
            submit.begin(state, Path('unused'), {'target': 'topic:another'})


class CleanupAndDispatchTests(unittest.TestCase):
    def test_running_ci_or_partial_submission_prevents_cleanup(self):
        state = store()
        with patch.object(topics, 'system_runs', return_value=[{'status': 'in_progress'}]), \
                patch.object(topics, 'write') as write:
            topics.clean_labels(state, {})
        write.assert_not_called()
        state.data['active'] = {'phase': 'blocked'}
        with patch.object(topics, 'system_runs') as runs:
            topics.clean_labels(state, {})
        runs.assert_not_called()

    def test_draft_member_in_another_repository_keeps_labels(self):
        state = store()
        with patch.object(topics, 'system_runs', return_value=[]), \
                patch.object(topics, 'pages', return_value=[{'name': 'topic:test'}]), \
                patch.object(topics, 'write') as write:
            topics.clean_labels(state, {'kernel': [], 'ci': [{**pull(), 'draft': True}]})
        write.assert_not_called()

    def test_closed_members_are_archived_before_labels_are_deleted(self):
        state = store()
        closed = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        item = dict(number=1, html_url='https://example.invalid', state='closed',
                    closed_at=closed, title='Done', pull_request={})
        order = []
        state.save.side_effect = lambda files: order.append('archive')
        with patch.object(topics, 'system_runs', return_value=[]), \
                patch.object(topics, 'pages', side_effect=[[{'name': 'topic:test'}], [item], []]), \
                patch.object(topics, 'inventory', return_value={'kernel': []}), \
                patch.object(topics, 'write', side_effect=lambda *a, **kw: order.append('delete')):
            topics.clean_labels(state, {'kernel': []})
        self.assertEqual(order, ['archive', 'delete'])

    def test_reopened_member_prevents_deletion_after_archive(self):
        state = store()
        closed = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        item = dict(number=1, html_url='https://example.invalid', state='closed',
                    closed_at=closed, title='Done', pull_request={})
        with patch.object(topics, 'system_runs', return_value=[]), \
                patch.object(topics, 'pages', side_effect=[[{'name': 'topic:test'}], [item]]), \
                patch.object(topics, 'inventory', return_value={'kernel': [pull()]}), \
                patch.object(topics, 'write') as write:
            topics.clean_labels(state, {'kernel': []})
        write.assert_not_called()

    def test_lost_dispatch_response_is_reconciled_without_duplicate(self):
        state = store()
        request = {'target': 'topic:test'}
        run = {'display_title': 'System verification: topic:test [kernel-123]',
               'status': 'completed', 'html_url': 'https://example.invalid/run'}
        with patch.object(topics, 'system_runs', return_value=[run]), patch.object(topics, 'write') as write:
            self.assertIn(run['html_url'], topics.dispatch_verification(state, request, 'kernel-123'))
        write.assert_not_called()

    def test_two_buttons_do_not_duplicate_a_dispatch_before_run_becomes_visible(self):
        state = store()
        state.data['requests']['kernel-1'] = {
            'request': {'target': 'topic:test'}, 'phase': 'done',
            'dispatched_at': datetime.now(timezone.utc).isoformat()}
        with patch.object(topics, 'system_runs', return_value=[]), patch.object(topics, 'write') as write:
            self.assertIn('awaiting', topics.dispatch_verification(state, {'target': 'topic:test'}, 'ci-2'))
        write.assert_not_called()


class StateTests(unittest.TestCase):
    def policy_fixture(self):
        rules = [dict(name=name, id=index, updated_at='2026-10-30T00:00:00Z', bypass_actors=actors)
                 for index, (name, actors) in enumerate(topic_store.STATE_ACTORS.items(), 1)]
        policy = {'version': 1, 'rules': {rule['name']: {
            key: rule[key] for key in ('id', 'updated_at', 'bypass_actors')} for rule in rules}}
        return rules, json.dumps(policy)

    def test_scoped_app_can_validate_redacted_rules_against_admin_policy(self):
        rules, policy = self.policy_fixture()
        for rule in rules:
            del rule['bypass_actors']
        topic_store.verify_policy(rules, policy)

    def test_changed_rules_or_actor_lists_fail_closed(self):
        for key, value in [('id', 999), ('updated_at', '2026-10-30T00:00:01Z'),
                           ('bypass_actors', [])]:
            rules, policy = self.policy_fixture()
            rules[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                topic_store.verify_policy(rules, policy)

    def test_missing_or_relaxed_admin_policy_is_rejected(self):
        rules, policy = self.policy_fixture()
        with self.assertRaises(ValueError):
            topic_store.verify_policy(rules, '')
        policy = json.loads(policy)
        policy['rules']['topic-state-writer']['bypass_actors'] = []
        del rules[0]['bypass_actors']
        with self.assertRaises(ValueError):
            topic_store.verify_policy(rules, json.dumps(policy))

    def test_unprotected_state_is_not_loaded(self):
        with patch.object(topic_store, 'pages', return_value=[]), patch.object(topic_store, 'api') as read:
            with self.assertRaisesRegex(ValueError, 'protect topic-state'):
                topic_store.Store()
        read.assert_not_called()

    def fixture(self):
        state = topic_store.Store.__new__(topic_store.Store)
        state.base = 'a' * 40
        state.data = {'version': 1, 'active': None}
        return state

    def test_state_update_never_forces_ref_and_uses_previous_parent(self):
        state = self.fixture()
        with patch.object(topic_store, 'api', return_value={'tree': {'sha': 'tree'}}), \
                patch.object(topic_store, 'write', side_effect=[{'sha': 'new-tree'}, {'sha': 'commit'}, {}]) as write:
            state.save()
        self.assertEqual(write.call_args_list[1].args[1]['parents'], ['a' * 40])
        self.assertEqual(write.call_args_list[2].args[1], {'sha': 'commit', 'force': False})
        self.assertEqual(state.base, 'commit')

    def test_lost_state_update_is_reconciled_but_another_writer_is_not_overwritten(self):
        for observed, accepted in [('commit', True), ('other', False)]:
            state = self.fixture()
            with self.subTest(observed=observed), \
                    patch.object(topic_store, 'api', side_effect=[{'tree': {'sha': 'tree'}}, {'object': {'sha': observed}}]), \
                    patch.object(topic_store, 'write', side_effect=[{'sha': 'new-tree'}, {'sha': 'commit'},
                        subprocess.CalledProcessError(1, ['gh'])]) as write:
                if accepted:
                    state.save()
                    self.assertEqual(state.base, 'commit')
                else:
                    with self.assertRaises(subprocess.CalledProcessError):
                        state.save()
                    self.assertEqual(state.base, 'a' * 40)
            self.assertEqual(write.call_count, 3)


if __name__ == '__main__':
    unittest.main()
