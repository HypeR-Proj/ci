# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import authorization as auth
import configure
import system_access as access
import topic_requests as requests
import topic_submit as submit
import topics


def membership(actor='roolrz', identifier=13791334):
    return {'state': 'active', 'organization': {'login': 'HypeR-Proj'},
            'user': {'login': actor, 'id': identifier}}


def owners(text='* @roolrz\n'):
    return {'type': 'file', 'encoding': 'base64',
            'content': base64.b64encode(text.encode()).decode()}


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        auth.trusted_ownership.cache_clear()

    def test_private_active_organization_members_are_allowed(self):
        with patch.object(auth, 'api', return_value=membership('contributor', 42)):
            self.assertEqual(auth.require_org_member('contributor')['id'], 42)

    def test_outside_collaborator_or_pending_member_is_not_an_org_member(self):
        denied = subprocess.CalledProcessError(1, ['gh'], stderr='HTTP 404')
        for value in (denied, {**membership(), 'state': 'pending'},
                      {**membership(), 'organization': {'login': 'Other'}}):
            kwargs = {'side_effect': value} if isinstance(value, Exception) else {'return_value': value}
            with patch.object(auth, 'api', **kwargs), self.assertRaises(ValueError):
                auth.require_org_member('roolrz')

    def test_other_admins_cannot_start_or_force_a_submission(self):
        state = Mock(data={'active': None})
        for force in (False, True):
            with patch.object(submit, 'permission', return_value=True), \
                    patch.object(submit, 'merge') as merge, self.assertRaisesRegex(ValueError, 'CODEOWNER'):
                submit.begin(state, Path('unused'), {'actor': 'another-admin'}, force)
            merge.assert_not_called()
            self.assertIsNone(state.data['active'])

    def test_resume_rechecks_actor_before_using_existing_snapshot(self):
        with patch.object(submit, 'verify_current') as current, self.assertRaisesRegex(ValueError, 'CODEOWNER'):
            submit.preflight({'projects': {}}, 'another-admin', force=True)
        current.assert_not_called()

    def test_ownership_uses_exact_trusted_base_not_proposed_head(self):
        base = 'a' * 40
        with patch.object(auth, 'require_submitter'), patch.object(auth, 'api', return_value=owners()) as api:
            auth.require_codeowner('kernel', 'roolrz', base)
        api.assert_called_once_with('repos/HypeR-Proj/kernel/contents/.github/CODEOWNERS?ref=' + base)
        with self.assertRaises(ValueError):
            auth.trusted_ownership('kernel', 'refs/heads/untrusted')

    def test_removed_ownership_path_override_or_symlink_is_rejected(self):
        for source in (owners('* @other\n'), owners('* @roolrz\nsecret/ @other\n'),
                       {**owners(), 'type': 'symlink'}):
            auth.trusted_ownership.cache_clear()
            with patch.object(auth, 'api', return_value=source), self.assertRaises(ValueError):
                auth.trusted_ownership('kernel', 'a' * 40)

    def test_actor_name_reuse_does_not_grant_authority(self):
        with patch.object(auth, 'api', return_value=membership(identifier=9)), self.assertRaises(ValueError):
            auth.require_submitter('roolrz')

    def test_verify_does_not_require_repository_write_but_submit_requires_owner(self):
        with patch.object(requests, 'require_org_member') as member, \
                patch.object(requests, 'permission') as permission:
            requests.authorize_request('kernel', {'action': 'verify', 'actor': 'contributor'})
        member.assert_called_once_with('contributor')
        permission.assert_not_called()
        with patch.object(requests, 'permission', return_value=True), self.assertRaisesRegex(ValueError, 'CODEOWNER'):
            requests.authorize_request('kernel', {'action': 'submit', 'actor': 'contributor'})

    def test_dispatch_cannot_bypass_org_check(self):
        with patch.object(topics, 'require_org_member', side_effect=ValueError('nonmember')), \
                patch.object(topics, 'write') as write, self.assertRaises(ValueError):
            topics.dispatch_verification(Mock(), {'actor': 'outside'}, 'kernel-1')
        write.assert_not_called()

    def test_only_owner_and_app_can_merge_or_execute_explicit_owner_overrides(self):
        history, verification, submission = configure.rulesets()
        self.assertEqual(history['bypass_actors'], [])
        self.assertEqual({(a['actor_type'], a['actor_id']) for a in submission['bypass_actors']},
                         {('User', configure.OWNER_ID), ('Integration', configure.APP_ID)})
        self.assertTrue(all(a['bypass_mode'] == 'pull_request' for a in submission['bypass_actors']))
        self.assertEqual(verification['bypass_actors'], [
            {'actor_id': configure.OWNER_ID, 'actor_type': 'User', 'bypass_mode': 'pull_request'},
            {'actor_id': configure.APP_ID, 'actor_type': 'Integration', 'bypass_mode': 'pull_request'}])
        rule = next(r for r in verification['rules'] if r['type'] == 'pull_request')['parameters']
        self.assertEqual(rule['required_approving_review_count'], 0)
        self.assertFalse(rule['require_code_owner_review'])

    def test_normal_submit_requires_tests_but_explicit_owner_override_only_skips_tests(self):
        snapshot = {'target': 'topic:test', 'projects': {'ci': {
            'pr': 1, 'base': 'a' * 40, 'head': 'b' * 40, 'merge_tree': 'c' * 40}}}
        pull = {'draft': False, 'mergeable': True, 'labels': [{'name': 'topic:test'}]}
        failure = {'name': 'module', 'status': 'completed', 'conclusion': 'failure'}
        with patch.object(submit, 'require_submitter'), patch.object(submit, 'trusted_ownership'), \
                patch.object(submit, 'verify_current', return_value=set()), \
                patch.object(submit, 'permission', return_value=True), \
                patch.object(submit, 'api', return_value=pull), \
                patch.object(submit, 'latest_check', return_value=failure) as checks:
            with self.assertRaisesRegex(ValueError, 'module has not passed'):
                submit.preflight(snapshot, 'roolrz')
            checks.reset_mock()
            submit.preflight(snapshot, 'roolrz', force=True)
            checks.assert_not_called()
            pull['mergeable'] = False
            with self.assertRaisesRegex(ValueError, 'conflicting'):
                submit.preflight(snapshot, 'roolrz', force=True)


class SystemAccessTests(unittest.TestCase):
    def run_record(self):
        return {'id': 123, 'run_attempt': 2, 'path': access.WORKFLOW,
                'event': 'workflow_dispatch',
                'triggering_actor': {'login': 'member', 'id': 42, 'type': 'User'}}

    def test_rerun_checks_current_triggering_actor_not_original_actor(self):
        run = {**self.run_record(), 'actor': {'login': 'roolrz', 'id': 13791334}}
        with patch.object(access, 'api', return_value=run), \
                patch.object(access, 'require_org_member', side_effect=ValueError('nonmember')) as member:
            result = access.authorize(123, 2)
        self.assertFalse(result['allowed'])
        member.assert_called_once_with('member')

    def test_bot_dispatch_requires_matching_recorded_member_request(self):
        run = {**self.run_record(), 'triggering_actor': {'login': access.BOT, 'type': 'Bot'},
               'display_title': 'System verification: topic:test [kernel-77]'}
        state = {'requests': {'kernel-77': {'phase': 'dispatching', 'request': {
            'actor': 'member', 'action': 'verify', 'target': 'topic:test'}}}}
        self.assertEqual(access.requester(run, state), {'login': 'member'})
        for field, value in [('action', 'submit'), ('target', 'topic:other')]:
            forged = copy.deepcopy(state)
            forged['requests']['kernel-77']['request'][field] = value
            with self.assertRaises(ValueError):
                access.requester(run, forged)
        with self.assertRaises(ValueError):
            access.requester(run, {'requests': {}})

    def test_untrusted_artifact_producer_is_rejected(self):
        artifact = {'name': 'test', 'workflow_run': {'id': 55}, 'size_in_bytes': 100}
        producer = {'id': 55, 'event': 'workflow_run', 'path': access.AUTHORIZER,
                    'head_branch': 'main', 'status': 'completed', 'conclusion': 'success'}
        self.assertTrue(access.trusted_artifact(artifact, producer, 'test'))
        for field, value in [('event', 'workflow_dispatch'), ('path', 'other.yml'),
                             ('head_branch', 'feature'), ('conclusion', 'failure')]:
            self.assertFalse(access.trusted_artifact(artifact, {**producer, field: value}, 'test'))

    def test_artifact_digest_run_attempt_actor_and_denial_are_checked(self):
        record = {'run_id': 123, 'attempt': 2, 'allowed': True, 'triggering_actor_id': 42}
        for change in ({}, {'attempt': 1}, {'run_id': 124}, {'allowed': False}, {'triggering_actor_id': 99}):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('authorization.json', json.dumps({**record, **change}))
            data = stream.getvalue()
            artifact = {'id': 1, 'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}
            with patch.object(access.subprocess, 'check_output', return_value=data):
                if change:
                    with self.assertRaises(ValueError):
                        access.read_evidence(artifact, self.run_record())
                else:
                    self.assertTrue(access.read_evidence(artifact, self.run_record())['allowed'])
                with self.assertRaises(ValueError):
                    access.read_evidence({**artifact, 'digest': 'sha256:bad'}, self.run_record())


if __name__ == '__main__':
    unittest.main()
