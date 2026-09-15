import base64
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from streamlit.proto.TextInput_pb2 import TextInput

from ir_graphrag.auth import hash_password
from ir_graphrag.auth_ui import get_auth_service


TEST_USERS_JSON = json.dumps({'reader': hash_password('ui-test-password'),
                              'reviewer': hash_password('ui-review-password')})


class UIAuthTests(unittest.TestCase):
    def setUp(self):
        auth_environment = patch.dict(os.environ, {'GRAPHRAG_AUTH_USERS_JSON': TEST_USERS_JSON})
        auth_environment.start()
        self.addCleanup(auth_environment.stop)
        get_auth_service.clear()
        self.addCleanup(get_auth_service.clear)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspaces = Path(temporary.name) / 'workspaces'
        root = workspaces / 'private-library'
        root.mkdir(parents=True)
        (root / 'settings.yaml').write_text('{}\n')
        (root / 'ingestion-report.json').write_text(json.dumps({
            'records_selected': 1, 'segments': 1, 'companies': {'ACME': 1}, 'issues': [],
        }))
        (root / 'answers').mkdir()
        (root / 'answers/saved.json').write_text(json.dumps({
            'question': 'Private question', 'answer': 'Private saved answer',
            'method': 'financial', 'evidence': [],
        }))
        project = Path(__file__).resolve().parents[1]
        self.source = (project / 'app.py').read_text().replace(
            'PROJECT = Path(__file__).resolve().parent', f'PROJECT = Path({str(project)!r})'
        ).replace("WORKSPACES = PROJECT / 'workspaces'", f'WORKSPACES = Path({str(workspaces)!r})')
        self.app = AppTest.from_string(self.source, default_timeout=10)

    def assert_locked(self):
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.text_input), 2)
        self.assertEqual(self.app.text_input('login_password').proto.type, TextInput.PASSWORD)
        self.assertEqual(self.app.text_input('login_password').value, '')
        self.assertFalse(self.app.sidebar.children)
        self.assertFalse(self.app.text_area)
        self.assertFalse(self.app.get('download_button'))
        self.assertNotIn('Private saved answer', [item.value for item in self.app.markdown])

    def login(self, username, password):
        self.app.text_input('login_username').set_value(username)
        self.app.text_input('login_password').set_value(password)
        self.app.button('login_submit').click().run()

    def test_anonymous_and_forged_query_cannot_read_workspaces(self):
        self.app.query_params.update({'auth_token': 'forged', 'username': 'reader'})
        with patch('pathlib.Path.glob') as glob, patch('subprocess.run') as command:
            self.app.run()
        glob.assert_not_called()
        command.assert_not_called()
        self.assert_locked()

    def test_missing_auth_configuration_stops_before_reading_workspaces(self):
        with patch.dict(os.environ, {'GRAPHRAG_AUTH_USERS_JSON': ''}), \
                patch('pathlib.Path.glob') as glob, patch('subprocess.run') as command:
            self.app.run()
        glob.assert_not_called()
        command.assert_not_called()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.error[0].value, 'Login is not configured. Contact the administrator.')
        self.assertFalse(self.app.text_input)
        self.assertFalse(self.app.sidebar.children)
        self.assertNotIn('Private saved answer', [item.value for item in self.app.markdown])

    def test_both_accounts_can_log_in_and_logout_revokes_and_clears_state(self):
        self.app.run()
        for username, password in [('reader', 'ui-test-password'), ('reviewer', 'ui-review-password')]:
            with self.subTest(username=username):
                self.login(username, password)
                self.assertFalse(self.app.exception)
                self.assertIn('Private saved answer', [item.value for item in self.app.markdown])
                token = self.app.session_state['auth_token']
                self.assertEqual(get_auth_service().current_user(token), username)
                self.assertNotIn('login_password', self.app.session_state)
                self.app.run()
                self.assertEqual(self.app.session_state['auth_token'], token)
                self.app.button('logout').click().run()
                self.assertIsNone(get_auth_service().current_user(token))
                self.assertNotIn('auth_token', self.app.session_state)
                self.assertNotIn('answer', self.app.session_state)
                self.assert_locked()

    def test_wrong_password_stays_locked_and_clears_password(self):
        self.app.query_params['lang'] = 'zh'
        self.app.run()
        self.login('reader', 'wrong-password')
        self.assert_locked()
        self.assertEqual(self.app.error[0].value, '用户名或密码错误。')
        self.assertNotIn('auth_token', self.app.session_state)

    def test_revoked_session_is_checked_again_before_showing_data(self):
        self.app.run()
        self.login('reader', 'ui-test-password')
        get_auth_service().logout(self.app.session_state['auth_token'])
        with patch('pathlib.Path.glob') as glob:
            self.app.run()
        glob.assert_not_called()
        self.assert_locked()
        self.assertNotIn('answer', self.app.session_state)

    def test_new_browser_session_requires_login(self):
        self.app.run()
        self.login('reviewer', 'ui-review-password')
        self.app = AppTest.from_string(self.source, default_timeout=10).run()
        self.assert_locked()

    def test_downloads_embed_json_without_public_file_urls(self):
        self.app.run()
        self.login('reviewer', 'ui-review-password')
        self.assertFalse(self.app.get('download_button'))
        downloads = [item.value for item in self.app.markdown if 'class="json-download"' in item.value]
        self.assertEqual(len(downloads), 2)
        answer_link = next(link for link in downloads if 'download="answer.json"' in link)
        encoded = re.search(r'href="data:application/json;base64,([^"]+)"', answer_link).group(1)
        self.assertEqual(json.loads(base64.b64decode(encoded))['answer'], 'Private saved answer')
        self.app.button('logout').click().run()
        self.assertFalse(any('class="json-download"' in item.value for item in self.app.markdown))


if __name__ == '__main__':
    unittest.main()
