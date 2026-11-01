# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

"""Exercise bounded publication, durable recovery and admission cost."""

from collections import deque
import copy
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import topic_submit as submit
from test_topics import pull, snapshot, store


class BatchSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.state = store()
        self.candidate = snapshot()
        self.merged = set()
        self.calls = []
        self.saved = []
        self.lock = threading.Lock()
        self.owner = threading.get_ident()
        self.checks = [
            {'id': 1, 'name': 'module', 'app': {'id': submit.ACTIONS_APP_ID},
             'status': 'completed', 'conclusion': 'success'},
            {'id': 2, 'name': 'system', 'app': {'id': submit.APP_ID},
             'status': 'completed', 'conclusion': 'success', 'external_id': 'test-identity'}]
        for name, options in {
            'require_submitter': {}, 'trusted_ownership': {},
            'permission': {'return_value': True},
            'api': {'side_effect': self.api}, 'pages': {'return_value': self.checks},
            'verify_current': {'side_effect': lambda _: set(self.merged)},
            'merge': {'side_effect': self.merge}, 'pace_batch': {},
        }.items():
            operation = patch.object(submit, name, **options)
            setattr(self, name + '_mock', operation.start())
            self.addCleanup(operation.stop)
        self.state.save.side_effect = self.save

    def configure(self, count, barriers=True):
        names = [f'module-{index:02d}' for index in range(count)]
        if barriers:
            names += ['manifest', 'ci']
        template = self.candidate['projects']['kernel']
        self.candidate['projects'] = {name: dict(template) for name in names}
        self.state.data['active'] = {
            'snapshot': self.candidate, 'actor': 'roolrz', 'force': False,
            'phase': 'running', 'inflight': None, 'error': None, 'merged': []}

    def save(self, *args):
        self.assertEqual(threading.get_ident(), self.owner)
        self.saved.append(copy.deepcopy(self.state.data))

    def api(self, path):
        name = path.split('/')[2]
        if '/pulls/' in path:
            return {**pull(), 'merged': name in self.merged}
        self.assertTrue(path.endswith('/commits/main'), path)
        return {'sha': self.candidate['projects'][name]['base']}

    def merge(self, name, candidate):
        with self.lock:
            self.assertEqual(candidate, self.candidate['projects'][name])
            self.assertIn(name, self.saved[-1]['active']['inflight'])
            self.calls.append(name)
            self.merged.add(name)
        return {'merged': True}

    def test_65_member_topic_has_bounded_global_reads_and_checkpoints(self):
        self.configure(63)
        self.state.data['active'] = None
        request = {'repository': 'manifest', 'head': 'b' * 40,
                   'target': 'topic:test', 'actor': 'roolrz'}
        with patch.object(submit, 'verified_snapshot', return_value=self.candidate):
            submit.begin(self.state, Path('unused'), request)
        submit.advance(self.state)
        self.assertEqual(len(self.calls), 65)
        self.assertEqual(len(set(self.calls)), 65)
        self.assertEqual(self.calls[0], 'manifest')
        self.assertEqual(self.calls[-1], 'ci')
        self.assertEqual(self.verify_current_mock.call_count, 23)
        self.assertEqual(self.state.save.call_count, 22)
        # Admission, resume validation and one batch admission each read all
        # check runs once, rather than separate module/system/additional reads.
        self.assertEqual(self.pages_mock.call_count, 65 * 3)
        self.assertEqual(self.pace_batch_mock.call_count, 10)
        archive = self.state.save.call_args.args[0]['submissions/test-identity.json']
        self.assertEqual(set(archive['merged']), set(self.candidate['projects']))
        self.assertIsNone(archive['inflight'])
        self.assertIsNone(self.state.data['active'])

    def test_four_workers_overlap_but_never_write_shared_state(self):
        self.configure(8)
        rendezvous = threading.Barrier(submit.MERGE_WORKERS, timeout=5)
        active = 0
        peak = 0

        def merge(name, candidate):
            nonlocal active, peak
            if name in ('manifest', 'ci'):
                self.assertEqual(active, 0)
                return self.merge(name, candidate)
            with self.lock:
                active += 1
                peak = max(peak, active)
            rendezvous.wait()
            result = self.merge(name, candidate)
            with self.lock:
                active -= 1
            return result

        self.merge_mock.side_effect = merge
        submit.advance(self.state)
        self.assertEqual(peak, 4)
        self.assertEqual(self.calls[0], 'manifest')
        self.assertEqual(self.calls[-1], 'ci')

    def test_partial_batch_stops_later_batches_and_resumes_only_missing_members(self):
        self.configure(16)

        def merge(name, candidate):
            if name == 'module-03':
                raise subprocess.CalledProcessError(1, ['gh'])
            return self.merge(name, candidate)

        self.merge_mock.side_effect = merge
        with self.assertRaisesRegex(ValueError, 'batch incomplete.*module-03'):
            submit.advance(self.state)
        self.assertEqual(self.state.data['active']['inflight'], ['module-03'])
        self.assertEqual(set(self.state.data['active']['merged']), self.merged)
        self.assertNotIn('module-08', self.calls)
        self.assertNotIn('ci', self.calls)
        confirmed = set(self.calls)
        self.merge_mock.side_effect = self.merge
        submit.advance(self.state)
        self.assertEqual(set(self.calls), set(self.candidate['projects']))
        for name in confirmed:
            self.assertEqual(self.calls.count(name), 1)

    def test_lost_response_for_completed_merge_does_not_interrupt_topic(self):
        self.configure(8)

        def merge(name, candidate):
            result = self.merge(name, candidate)
            if name == 'module-03':
                raise subprocess.CalledProcessError(1, ['gh'])
            return result

        self.merge_mock.side_effect = merge
        submit.advance(self.state)
        self.assertIsNone(self.state.data['active'])
        self.assertEqual(len(self.calls), 10)

    def test_checkpoint_failure_prevents_any_batch_request(self):
        self.configure(8, barriers=False)
        self.state.save.side_effect = RuntimeError('checkpoint unavailable')
        with self.assertRaisesRegex(RuntimeError, 'checkpoint unavailable'):
            submit.advance(self.state)
        self.merge_mock.assert_not_called()

    def test_crash_after_merges_before_checkpoint_recovers_complete_archive(self):
        self.configure(8, barriers=False)

        def save(*args):
            if self.saved:
                raise RuntimeError('checkpoint unavailable')
            self.save(*args)

        self.state.save.side_effect = save
        with self.assertRaisesRegex(RuntimeError, 'checkpoint unavailable'):
            submit.advance(self.state)
        self.assertEqual(len(self.merged), 8)
        self.state.data = copy.deepcopy(self.saved[0])
        self.state.save.side_effect = self.save
        self.merge_mock.reset_mock()
        submit.advance(self.state)
        self.merge_mock.assert_not_called()
        archive = self.state.save.call_args.args[0]['submissions/test-identity.json']
        self.assertEqual(set(archive['merged']), self.merged)
        self.assertIsNone(archive['inflight'])

    def test_changed_candidate_after_rate_wait_stops_before_next_batch(self):
        self.configure(16, barriers=False)

        def pace(*args):
            if self.calls:
                self.verify_current_mock.side_effect = ValueError('topic membership changed')

        self.pace_batch_mock.side_effect = pace
        with self.assertRaisesRegex(ValueError, 'membership changed'):
            submit.advance(self.state)
        self.assertEqual(len(self.calls), 8)

    def test_failed_gate_after_initial_admission_stops_next_batch(self):
        self.configure(16, barriers=False)

        def pace(*args):
            if self.calls:
                self.pages_mock.return_value = [
                    {**self.checks[0], 'id': 3, 'conclusion': 'failure'}, self.checks[1]]

        self.pace_batch_mock.side_effect = pace
        with self.assertRaisesRegex(ValueError, 'module has not passed'):
            submit.advance(self.state)
        self.assertEqual(len(self.calls), 8)

    def test_every_member_must_pass_even_if_failure_is_in_last_batch(self):
        self.configure(16)

        def checks(path, key):
            if '/ci/' in path:
                return [{**self.checks[0], 'conclusion': 'failure'}, self.checks[1]]
            return self.checks

        self.pages_mock.side_effect = checks
        with self.assertRaisesRegex(ValueError, 'module has not passed'):
            submit.advance(self.state)
        self.merge_mock.assert_not_called()

    def test_combined_read_keeps_system_identity_app_and_latest_result_guards(self):
        self.configure(8)
        for checks, message in (
            ([self.checks[0], {**self.checks[1], 'app': {'id': 42}}], 'system check is missing'),
            ([self.checks[0], {**self.checks[1], 'external_id': 'another-topic'}], 'same complete topic'),
            (self.checks + [{**self.checks[0], 'id': 3, 'conclusion': 'failure'}], 'module has not passed'),
        ):
            with self.subTest(message=message):
                self.pages_mock.return_value = checks
                with self.assertRaisesRegex(ValueError, message):
                    submit.advance(self.state)
                self.merge_mock.assert_not_called()
                self.state.save.assert_not_called()

    def test_declined_batch_preserves_reason_and_unconfirmed_members(self):
        self.configure(2, barriers=False)
        self.merge_mock.side_effect = None
        self.merge_mock.return_value = {'merged': False, 'message': 'Required status check pending'}
        with self.assertRaisesRegex(ValueError, 'batch incomplete.*Required status check pending'):
            submit.advance(self.state)
        self.assertEqual(self.state.data['active']['inflight'], ['module-00', 'module-01'])
        self.assertEqual(self.state.data['active']['merged'], [])

    def test_unexpected_tree_after_batch_preserves_inflight_and_stops(self):
        self.configure(16, barriers=False)

        def verify(_):
            if self.merged:
                raise ValueError('main changed; candidate verification is stale')
            return set()

        self.verify_current_mock.side_effect = verify
        with self.assertRaisesRegex(ValueError, 'main changed'):
            submit.advance(self.state)
        self.assertEqual(len(self.calls), 8)
        self.assertEqual(self.state.data['active']['inflight'], sorted(self.merged))
        self.assertEqual(len(self.saved), 1)


class BatchProtocolTests(unittest.TestCase):
    def test_merge_still_binds_head_and_uses_squash(self):
        candidate = snapshot()['projects']['kernel']
        with patch.object(submit, 'write', return_value={'merged': True}) as write:
            submit.merge('kernel', candidate)
        self.assertEqual(write.call_args.args, (
            'repos/HypeR-Proj/kernel/pulls/1/merge',
            {'sha': 'b' * 40, 'merge_method': 'squash'}, 'PUT'))

    def test_batch_reconciles_partial_metadata_without_remerging_confirmed_peer(self):
        active = {'snapshot': snapshot(), 'inflight': ['kernel', 'ci']}
        head = {'sha': 'd' * 40, 'commit': {'tree': {'sha': 'c' * 40}},
                'parents': [{'sha': 'a' * 40}]}
        with patch.object(submit, 'api', side_effect=[
                {'merged': True}, {**pull(), 'merged': False}, head]), \
                patch.object(submit, 'merge') as merge:
            submit.reconcile_inflight(active)
        merge.assert_called_once_with('ci', active['snapshot']['projects']['ci'])

    def test_pacing_allows_small_topics_then_waits_without_a_real_sleep(self):
        now = 0
        waits = []

        def sleep(seconds):
            nonlocal now
            waits.append(seconds)
            now += seconds

        recent = deque()
        with patch.object(submit.time, 'monotonic', side_effect=lambda: now), \
                patch.object(submit.time, 'sleep', side_effect=sleep):
            for count in (7, 14, 14, 14):
                submit.pace_batch(recent, count)
            self.assertEqual(waits, [])
            submit.pace_batch(recent, 14)
        self.assertEqual(waits, [60])
        self.assertEqual(list(recent), [(60, 14)])


if __name__ == '__main__':
    unittest.main()
