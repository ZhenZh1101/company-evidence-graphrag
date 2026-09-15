import json
import os
import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from ir_graphrag.auth import hash_password
from ir_graphrag.auth_ui import get_auth_service
from ir_graphrag.cli import main
from ir_graphrag.engine import PROVIDERS


TEST_USERS_JSON = json.dumps({'reader': hash_password('ui-test-password'),
                              'reviewer': hash_password('ui-review-password')})


class ProviderUITests(unittest.TestCase):
    def test_cli_passes_independent_provider_options_and_chat_only_check(self):
        options = dict(provider='deepseek', model='custom-chat', api_base='https://chat.example.test/v1',
                       api_key_env='CUSTOM_CHAT_KEY', embedding_provider='openai', embedding_model='custom-embedding',
                       embedding_api_base='https://embedding.example.test/v1', embedding_api_key_env='CUSTOM_EMBEDDING_KEY',
                       vector_size=1024)
        flags = [part for key, value in options.items() for part in ('--' + key.replace('_', '-'), str(value))]
        root = Path('/tmp/provider-cli-check').resolve()
        with patch('sys.argv', ['ir-graphrag', 'init', '--root', str(root), *flags]), \
                patch('ir_graphrag.engine.initialize') as initialize, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        initialize.assert_called_once_with(root=root, **options)
        with patch('sys.argv', ['ir-graphrag', 'init', '--root', str(root)]), \
                patch('ir_graphrag.engine.initialize') as initialize, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        self.assertIsNone(initialize.call_args.kwargs['vector_size'])
        with patch('sys.argv', ['ir-graphrag', 'doctor', '--root', str(root), '--chat-only']), \
                patch('ir_graphrag.engine.doctor', return_value={}) as doctor, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        doctor.assert_called_once_with(root=root, chat_only=True)

    def test_provider_defaults_reset_and_reach_init_command(self):
        auth_environment = patch.dict(os.environ, {'GRAPHRAG_AUTH_USERS_JSON': TEST_USERS_JSON})
        auth_environment.start()
        self.addCleanup(auth_environment.stop)
        get_auth_service.clear()
        self.addCleanup(get_auth_service.clear)
        with tempfile.TemporaryDirectory() as temp:
            app_file = Path(temp) / 'app.py'
            app_file.write_text((Path(__file__).resolve().parents[1] / 'app.py').read_text())
            app = AppTest.from_file(str(app_file)).run()
            app.text_input('login_username').input('reader')
            app.text_input('login_password').input('ui-test-password')
            app.button('login_submit').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.text_input(key='init-embedding-model:openclaw:openclaw').value,
                             'openai/text-embedding-3-large')
            self.assertEqual(app.number_input(key='init-vector-size:openclaw:openclaw').value, 3072)
            app.selectbox(key='init-provider').select('deepseek').run()
            self.assertEqual(app.text_input(key='init-model:deepseek').value, PROVIDERS['deepseek']['model'])
            self.assertEqual(app.selectbox(key='init-embedding-provider:deepseek').value, 'openai')
            self.assertEqual(app.text_input(key='init-embedding-model:deepseek:openai').value,
                             'text-embedding-3-large')
            self.assertEqual(app.number_input(key='init-vector-size:deepseek:openai').value, 3072)
            app.text_input(key='init-model:deepseek').input('temporary-custom-model').run()
            app.selectbox(key='init-provider').select('zai').run()
            self.assertEqual(app.text_input(key='init-api-base:zai').value, PROVIDERS['zai']['api_base'])
            app.selectbox(key='init-provider').select('deepseek').run()
            self.assertEqual(app.text_input(key='init-model:deepseek').value, PROVIDERS['deepseek']['model'])
            app.selectbox(key='init-embedding-provider:deepseek').select('openclaw').run()
            self.assertEqual(app.text_input(key='init-embedding-api-base:deepseek:openclaw').value,
                             PROVIDERS['openclaw']['api_base'])
            app.text_input(key='init-model:deepseek').input('custom-chat')
            app.text_input(key='init-api-base:deepseek').input('https://chat.example.test/v1')
            app.text_input(key='init-embedding-model:deepseek:openclaw').input('custom-embedding')
            app.text_input(key='init-embedding-api-base:deepseek:openclaw').input('https://embedding.example.test/v1')
            app.number_input(key='init-vector-size:deepseek:openclaw').set_value(1024)
            app.text_input(key='import_name').input('provider-test')
            app.text_area(key='import_datasets').input('/documents')
            with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'mocked init')) as run:
                next(button for button in app.button if button.label in ('导入文档', 'Import documents')).click().run()
            self.assertFalse(app.exception)
            command = run.call_args.args[0]
            self.assertEqual(command[2:6], ['ir_graphrag.cli', 'init', '--root', str(Path(temp).resolve() / 'workspaces/provider-test')])
            self.assertEqual(command[6:], [
                '--provider', 'deepseek', '--model', 'custom-chat', '--api-base', 'https://chat.example.test/v1',
                '--embedding-provider', 'openclaw', '--embedding-model', 'custom-embedding',
                '--embedding-api-base', 'https://embedding.example.test/v1', '--vector-size', '1024',
            ])


if __name__ == '__main__':
    unittest.main()
