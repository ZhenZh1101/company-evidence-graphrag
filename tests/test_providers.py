import asyncio
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from ir_graphrag import engine, financial


class ProviderTests(unittest.TestCase):
    def test_provider_presets_and_default_openclaw_remain_loadable(self):
        presets = [
            (None, 'openclaw/llm-gpt55', 'http://127.0.0.1:18789/v1', 'GRAPHRAG_API_KEY', 'ir_openclaw'),
            ('openai', 'gpt-4.1-mini', 'https://api.openai.com/v1', 'OPENAI_API_KEY', 'litellm'),
            ('zai', 'glm-4.7', 'https://api.z.ai/api/paas/v4', 'ZAI_API_KEY', 'ir_json_chat'),
            ('deepseek', 'deepseek-flash', 'https://api.deepseek.com', 'DEEPSEEK_API_KEY', 'ir_json_chat'),
        ]
        for provider, model, endpoint, key_env, transport in presets:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                engine.initialize(root, **({'provider': provider} if provider else {}))
                keys = {key_env: 'chat-test-key'}
                if provider in {'zai', 'deepseek'}:
                    keys['OPENAI_API_KEY'] = 'embedding-test-key'
                with patch.dict('os.environ', keys, clear=True):
                    config = engine.load_settings(root)
                chat = config.completion_models['default_completion_model']
                embedding = config.embedding_models['default_embedding_model']
                self.assertEqual((chat.model, chat.api_base, chat.type), (model, endpoint, transport))
                self.assertEqual(chat.api_key, 'chat-test-key')
                self.assertEqual(embedding.model, 'text-embedding-3-large' if provider else 'openclaw/llm-gpt55')
                self.assertEqual(embedding.api_base, 'https://api.openai.com/v1' if provider else endpoint)
                self.assertEqual(embedding.api_key, keys['OPENAI_API_KEY'] if provider else keys[key_env])
                self.assertEqual(config.vector_store.vector_size, 3072)
                self.assertNotIn('extra_headers', chat.call_args)
                self.assertEqual(embedding.call_args.get('extra_headers', {}),
                                 {} if provider else {'x-openclaw-model': 'openai/text-embedding-3-large'})

    def test_mixed_providers_and_custom_endpoints_keep_keys_separate(self):
        for chat_provider, embedding_provider in [('openai', 'openclaw'), ('deepseek', 'openai')]:
            with self.subTest(chat=chat_provider, embedding=embedding_provider), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                engine.initialize(root, provider=chat_provider, embedding_provider=embedding_provider,
                                  model='custom-chat', embedding_model='custom-embedding',
                                  api_base='https://chat.example.test/v1/',
                                  embedding_api_base='https://embedding.example.test/v1/',
                                  api_key_env='CUSTOM_CHAT_KEY', embedding_api_key_env='CUSTOM_EMBEDDING_KEY',
                                  vector_size=1024)
                with patch.dict('os.environ', {'CUSTOM_CHAT_KEY': 'chat-key', 'CUSTOM_EMBEDDING_KEY': 'embed-key'}, clear=True):
                    config = engine.load_settings(root)
                chat = config.completion_models['default_completion_model']
                embedding = config.embedding_models['default_embedding_model']
                self.assertEqual((chat.model, chat.api_base, chat.api_key),
                                 ('custom-chat', 'https://chat.example.test/v1', 'chat-key'))
                self.assertEqual((embedding.model, embedding.api_base, embedding.api_key),
                                 ('openclaw/llm-gpt55' if embedding_provider == 'openclaw' else 'custom-embedding',
                                  'https://embedding.example.test/v1', 'embed-key'))
                self.assertNotIn('extra_headers', chat.call_args)
                self.assertEqual(embedding.call_args.get('extra_headers', {}),
                                 {'x-openclaw-model': 'custom-embedding'} if embedding_provider == 'openclaw' else {})
                self.assertEqual(config.vector_store.vector_size, 1024)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine.initialize(root, provider='openai', api_base='https://shared.example.test/v1/', api_key_env='SHARED_KEY')
            with patch.dict('os.environ', {'SHARED_KEY': 'shared-key'}, clear=True):
                config = engine.load_settings(root)
            embedding = config.embedding_models['default_embedding_model']
            self.assertEqual((embedding.api_base, embedding.api_key), ('https://shared.example.test/v1', 'shared-key'))

    def test_embedding_cache_namespace_changes_with_model_and_dimensions(self):
        namespaces = []
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict('os.environ', {'GRAPHRAG_API_KEY': 'test-key'}, clear=True):
            for i, options in enumerate(({}, {'embedding_model': 'custom-embedding'}, {'vector_size': 1024}, {})):
                root = Path(temp) / str(i)
                engine.initialize(root, **options)
                namespace = engine.load_settings(root).embed_text.model_instance_name
                self.assertRegex(namespace, r'^text_embedding_[0-9a-f]{16}$')
                namespaces.append(namespace)
        self.assertEqual(len(set(namespaces[:3])), 3)
        self.assertEqual(namespaces[0], namespaces[3])

    def test_openclaw_embedding_sync_and_async_http_transport(self):
        from graphrag_llm.embedding.embedding_factory import create_embedding

        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, self.headers.get('Authorization'),
                                 self.headers.get('x-openclaw-model'), body))
                payload = json.dumps({
                    'object': 'list', 'model': 'text-embedding-3-large',
                    'data': [{'object': 'embedding', 'index': i, 'embedding': [float(i + 1)] * 3072}
                             for i in range(len(body['input']))],
                    'usage': {'prompt_tokens': 2, 'total_tokens': 2},
                }).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with tempfile.TemporaryDirectory() as temp, \
                     patch.dict('os.environ', {'GRAPHRAG_API_KEY': 'transport-test-key'}, clear=True):
                    root = Path(temp)
                    engine.initialize(root, embedding_provider='openclaw',
                                      embedding_api_base=f'http://127.0.0.1:{server.server_port}/v1')
                    config = engine.load_settings(root)
                    model = create_embedding(config.embedding_models['default_embedding_model'])
                    inputs = ['文本1', '文本2']
                    sync = model.embedding(input=inputs)
                    async_result = asyncio.run(model.embedding_async(input=inputs))
                    for result in (sync, async_result):
                        self.assertEqual(result.embeddings, [[1.0] * 3072, [2.0] * 3072])
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual(len(requests), 2)
        for path, auth, route, body in requests:
            self.assertEqual((path, auth, route),
                             ('/v1/embeddings', 'Bearer transport-test-key', 'openai/text-embedding-3-large'))
            self.assertEqual(body['model'], 'openclaw/llm-gpt55')
            self.assertEqual(body['input'], inputs)

    def test_process_keys_override_dotenv_and_dollar_characters_are_literal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine.initialize(root, provider='deepseek')
            (root / '.env').write_text("DEEPSEEK_API_KEY='file-chat'\nOPENAI_API_KEY='file-${UNSET}-$cash'\nUNRELATED=${MISSING}\n")
            with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'process-${ALSO_UNSET}-$cash'}, clear=True):
                config = engine.load_settings(root)
            self.assertEqual(config.completion_models['default_completion_model'].api_key, 'process-${ALSO_UNSET}-$cash')
            self.assertEqual(config.embedding_models['default_embedding_model'].api_key, 'file-${UNSET}-$cash')

    def test_openclaw_config_fallback_is_limited_to_local_endpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            gateway = base / 'gateway.json'
            gateway.write_text(json.dumps({'gateway': {'auth': {'token': 'local-test-token'}}}))
            for provider in ('openclaw', 'openai', 'zai', 'deepseek'):
                root = base / provider
                engine.initialize(root, provider=provider, api_key_env='GRAPHRAG_API_KEY')
                for key in ('', '<API_KEY>'):
                    with self.subTest(provider=provider, key=key), patch.dict('os.environ', {
                        'OPENCLAW_CONFIG': str(gateway), 'GRAPHRAG_API_KEY': key,
                    }, clear=True):
                        if provider == 'openclaw':
                            config = engine.load_settings(root, require_embeddings=False)
                            self.assertEqual(config.completion_models['default_completion_model'].api_key, 'local-test-token')
                        else:
                            with self.assertRaisesRegex(ValueError, 'Set GRAPHRAG_API_KEY'):
                                engine.load_settings(root, require_embeddings=False)

    def test_financial_answer_needs_only_its_chat_credentials(self):
        async def stream(question):
            yield 'Revenue was USD 100 million. [Data: Sources (1)]'

        for provider, key_env in [('zai', 'ZAI_API_KEY'), ('deepseek', 'DEEPSEEK_API_KEY')]:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temp, \
                 patch.dict('os.environ', {key_env: 'chat-test-key'}, clear=True):
                root = Path(temp)
                engine.initialize(root, provider=provider)
                config = engine.load_settings(root, require_embeddings=False)
                self.assertEqual(config.embedding_models, {})
                with self.assertRaisesRegex(ValueError, 'Set OPENAI_API_KEY'):
                    engine.load_settings(root)
                evidence = {'source_id': '1', 'title': 'Results', 'excerpt': 'Revenue: USD 100 million.'}
                with patch.object(financial, 'search', return_value={'question': 'Revenue?', 'evidence': [evidence]}), \
                     patch('graphrag_llm.completion.completion_factory.create_completion') as completion, \
                     patch('graphrag.query.structured_search.basic_search.search.BasicSearch') as search:
                    search.return_value.stream_search.side_effect = stream
                    result = financial.answer(root, 'Revenue?')
                self.assertEqual(completion.call_args.args[0].api_key, 'chat-test-key')
                self.assertTrue(result['evidence'][0]['cited'])
                self.assertEqual(result['citation_audit']['status'], 'verified_ids')

    def test_invalid_initialization_does_not_create_workspace(self):
        options = [
            {'provider': 'unknown'}, {'embedding_provider': 'zai'}, {'embedding_provider': 'deepseek'},
            {'api_key_env': 'not-an-env-name'}, {'embedding_api_key_env': 'KEY=secret'},
            {'vector_size': 0}, {'vector_size': -1},
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'workspace'
            for kwargs in options:
                with self.subTest(options=kwargs), self.assertRaises(ValueError):
                    engine.initialize(root, **kwargs)
                self.assertFalse(root.exists())

    def test_initialization_preserves_existing_dotenv_values(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = "# Existing workspace settings\nOPENAI_API_KEY='saved-$literal'\nCUSTOM_OPTION=keep-me\n"
            (root / '.env').write_text(original)
            engine.initialize(root, provider='openai')
            self.assertTrue((root / '.env').read_text().startswith(original))
            self.assertEqual((root / '.env').stat().st_mode & 0o777, 0o600)
            with patch.dict('os.environ', {}, clear=True):
                config = engine.load_settings(root)
            self.assertEqual(config.completion_models['default_completion_model'].api_key, 'saved-$literal')
            self.assertEqual(config.embedding_models['default_embedding_model'].api_key, 'saved-$literal')

    def test_doctor_uses_configured_factories_and_checks_json_and_vectors(self):
        from graphrag_llm.types import LLMEmbeddingResponse
        from graphrag_llm.utils import create_completion_response

        def completion_response(*, messages, response_format):
            response = create_completion_response('{"ok":true}')
            response.formatted_response = response_format(ok=True)
            return response

        def embedding_response(vectors):
            return LLMEmbeddingResponse(object='list', model='test-embedding',
                                        data=[{'object': 'embedding', 'index': i, 'embedding': v} for i, v in enumerate(vectors)],
                                        usage={'prompt_tokens': 0, 'total_tokens': 0})

        with tempfile.TemporaryDirectory() as temp, \
             patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'chat-key', 'OPENAI_API_KEY': 'embed-key'}, clear=True), \
             patch('graphrag_llm.completion.completion_factory.create_completion') as completion, \
             patch('graphrag_llm.embedding.embedding_factory.create_embedding') as embedding:
            root = Path(temp)
            engine.initialize(root, provider='deepseek', vector_size=2)
            completion.return_value.completion.side_effect = completion_response
            embedding.return_value.embedding.return_value = embedding_response([[1.0, 0.0], [0.0, 1.0]])
            result = engine.doctor(root)
            self.assertEqual((result['chat_json'], result['embeddings'], result['dimensions']), (True, True, 2))
            self.assertEqual(completion.call_args.args[0].type, 'ir_json_chat')
            self.assertEqual(completion.call_args.args[0].api_key, 'chat-key')
            self.assertEqual(embedding.call_args.args[0].api_key, 'embed-key')
            gateway_root = root / 'gateway'
            engine.initialize(gateway_root, vector_size=2)
            with patch.dict('os.environ', {'GRAPHRAG_API_KEY': 'gateway-key'}):
                self.assertEqual(engine.doctor(gateway_root)['embedding_model'], 'openai/text-embedding-3-large')
            for vectors in ([[1.0], [2.0]], [[1.0, 0.0]], [[1.0, 0.0], [1.0, 0.0]],
                            [[0.0, 0.0], [0.0, 1.0]], [[float('nan'), 1.0], [0.0, 1.0]]):
                with self.subTest(vectors=vectors), self.assertRaisesRegex(ValueError, 'Invalid embeddings'):
                    embedding.return_value.embedding.return_value = embedding_response(vectors)
                    engine.doctor(root)
            embedding.reset_mock()
            with patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'chat-key'}, clear=True):
                self.assertEqual(engine.doctor(root, chat_only=True)['embeddings'], False)
            embedding.assert_not_called()
            completion.return_value.completion.side_effect = None
            completion.return_value.completion.return_value = create_completion_response('not JSON')
            with self.assertRaisesRegex(ValueError, 'requested JSON object'):
                engine.doctor(root, chat_only=True)


if __name__ == '__main__':
    unittest.main()
