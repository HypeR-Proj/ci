# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import documentation


class DocumentationTests(unittest.TestCase):
    def topic(self, name='topic:docs'):
        return {'labels': [{'name': name}]}

    def candidate(self):
        return {**self.topic(), 'state': 'open', 'base': {'ref': 'main'},
                'mergeable': True, 'merge_commit_sha': 'c' * 40}

    def test_docs_repository_uses_its_checked_out_candidate(self):
        with patch.object(documentation, 'api') as api:
            self.assertEqual(documentation.tools_revision('docs', 2, 'a' * 40), 'a' * 40)
            api.assert_not_called()
        with self.assertRaises(ValueError):
            documentation.tools_revision('docs', 2, 'refs/heads/main')

    def test_publication_and_unrelated_prs_use_main_tools(self):
        with patch.object(documentation, 'api', return_value={'labels': []}) as api:
            self.assertEqual(documentation.tools_revision('kernel', 0, 'a' * 40), 'main')
            api.assert_not_called()
            self.assertEqual(documentation.tools_revision('kernel', 2, 'a' * 40), 'main')
        with patch.object(documentation, 'api', return_value=self.topic()), \
                patch.object(documentation, 'open_pulls', return_value=[]):
            self.assertEqual(documentation.tools_revision('kernel', 2, 'a' * 40), 'main')

    def test_topic_uses_documentation_merge_commit(self):
        with patch.object(documentation, 'api', side_effect=[self.topic(), self.candidate()]) as api, \
                patch.object(documentation, 'open_pulls', return_value=[{**self.topic(), 'number': 8}]):
            self.assertEqual(documentation.tools_revision('kernel', 2, 'a' * 40), 'c' * 40)
        self.assertEqual(api.call_args_list[-1].args, ('repos/HypeR-Proj/docs/pulls/8',))

    def test_ambiguous_or_changed_topic_does_not_silently_use_old_tools(self):
        with patch.object(documentation, 'api', return_value={'labels': [{'name': 'topic:a'}, {'name': 'topic:b'}]}), \
                self.assertRaisesRegex(ValueError, 'only one topic'):
            documentation.tools_revision('kernel', 2, 'a' * 40)
        with patch.object(documentation, 'api', return_value=self.topic()), \
                patch.object(documentation, 'open_pulls', return_value=[{**self.topic(), 'number': 8}] * 2), \
                self.assertRaisesRegex(ValueError, 'at most one'):
            documentation.tools_revision('kernel', 2, 'a' * 40)
        for changed in ({'mergeable': None}, {'mergeable': False}, {'merge_commit_sha': None},
                        {'state': 'closed'}, {'labels': []}, {'base': {'ref': 'other'}}):
            with self.subTest(changed=changed), \
                    patch.object(documentation, 'api', side_effect=[self.topic(), {**self.candidate(), **changed}]), \
                    patch.object(documentation, 'open_pulls', return_value=[{**self.topic(), 'number': 8}]), \
                    self.assertRaisesRegex(ValueError, 'candidate changed'):
                documentation.tools_revision('kernel', 2, 'a' * 40)


if __name__ == '__main__':
    unittest.main()
