import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from graphrag_cache.memory_cache import MemoryCache
from graphrag_llm.completion import create_completion
from graphrag_llm.config import ModelConfig
from litellm import ModelResponse
from pydantic import BaseModel, ValidationError

from ir_graphrag.json_completion import register


class ExtractedFact(BaseModel):
    company: str
    value: int


class JsonCompletionTests(unittest.TestCase):
    def setUp(self):
        register()
        self.config = ModelConfig(
            type='ir_json_chat', model_provider='openai', model='glm-4.7',
            api_base='https://api.z.ai/api/paas/v4/', api_key='test-key',
            metrics=None,
        )
        self.model = create_completion(self.config, tokenizer=Mock())

    def call(self, asynchronous, **kwargs):
        if asynchronous:
            return asyncio.run(self.model.completion_async(**kwargs))
        return self.model.completion(**kwargs)

    def transport(self, asynchronous, content):
        response = ModelResponse(model='test', choices=[{
            'index': 0, 'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': content},
        }])
        return patch(
            'litellm.acompletion' if asynchronous else 'litellm.completion',
            new_callable=AsyncMock if asynchronous else Mock,
            return_value=response,
        )

    def test_structured_requests_use_json_mode_and_validate_original_schema(self):
        messages = [{'role': 'user', 'content': 'Extract the company and value.'}]
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                with self.transport(asynchronous, '{"company":"ACME","value":12}') as send:
                    response = self.call(
                        asynchronous, messages=messages, response_format=ExtractedFact,
                    )
                self.assertIsInstance(response.formatted_response, ExtractedFact)
                self.assertEqual(response.formatted_response.value, 12)
                request = send.call_args.kwargs
                self.assertEqual(request['response_format'], {'type': 'json_object'})
                instruction = request['messages'][0]['content']
                self.assertIn('JSON object', instruction)
                self.assertEqual(
                    json.loads(instruction.split('JSON Schema:\n', 1)[1]),
                    ExtractedFact.model_json_schema(),
                )
                self.assertEqual(request['messages'][1:], messages)
                self.assertEqual(len(messages), 1)

                for content, error in (('{"company":"ACME"}', ValidationError), ('', json.JSONDecodeError)):
                    with self.transport(asynchronous, content), self.assertRaises(error):
                        self.call(asynchronous, messages=messages, response_format=ExtractedFact)

    def test_global_search_json_flag_and_plain_calls(self):
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                with self.transport(asynchronous, '{"points": []}') as send:
                    response = self.call(
                        asynchronous, messages='Summarize.', response_format_json_object=True,
                    )
                self.assertIsNone(response.formatted_response)
                self.assertEqual(send.call_args.kwargs['response_format'], {'type': 'json_object'})
                self.assertIn('JSON', send.call_args.kwargs['messages'][0]['content'])
                self.assertNotIn('response_format_json_object', send.call_args.kwargs)

                with self.transport(asynchronous, 'Plain response') as send:
                    response = self.call(asynchronous, messages='Question', temperature=0.3)
                self.assertEqual(response.content, 'Plain response')
                self.assertEqual(send.call_args.kwargs['messages'], [{'role': 'user', 'content': 'Question'}])
                self.assertEqual(send.call_args.kwargs['temperature'], 0.3)
                self.assertNotIn('response_format', send.call_args.kwargs)

    def test_streaming_passes_through_and_rejects_pydantic_formats(self):
        chunk = Mock()
        chunk.model_dump.return_value = {
            'id': 'chunk', 'created': 0, 'model': 'test', 'object': 'chat.completion.chunk',
            'choices': [{'index': 0, 'delta': {'content': 'Hello'}, 'finish_reason': None}],
        }

        async def chunks():
            yield chunk

        async def collect():
            stream = await self.model.completion_async(messages='Question', stream=True)
            return [part async for part in stream]

        with patch('litellm.completion', return_value=iter([chunk])) as send:
            result = list(self.model.completion(messages='Question', stream=True))
        self.assertEqual(result[0].choices[0].delta.content, 'Hello')
        self.assertTrue(send.call_args.kwargs['stream'])
        self.assertNotIn('response_format', send.call_args.kwargs)

        with patch('litellm.acompletion', new_callable=AsyncMock, return_value=chunks()) as send:
            result = asyncio.run(collect())
        self.assertEqual(result[0].choices[0].delta.content, 'Hello')
        self.assertTrue(send.call_args.kwargs['stream'])
        self.assertNotIn('response_format', send.call_args.kwargs)

        for asynchronous in (False, True):
            with self.assertRaisesRegex(ValueError, 'streaming'):
                self.call(asynchronous, messages='Question', stream=True, response_format=ExtractedFact)

    def test_invalid_structured_response_is_not_cached(self):
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                self.model = create_completion(self.config, tokenizer=Mock(), cache=MemoryCache())
                for content, error in (('{"company":"ACME"}', ValidationError), ('', json.JSONDecodeError)):
                    with self.transport(asynchronous, content) as send, self.assertRaises(error):
                        self.call(asynchronous, messages='Extract.', response_format=ExtractedFact)
                    self.assertEqual(send.call_count, 1)
                with self.transport(asynchronous, '{"company":"ACME","value":12}') as send:
                    response = self.call(asynchronous, messages='Extract.', response_format=ExtractedFact)
                    cached = self.call(asynchronous, messages='Extract.', response_format=ExtractedFact)
                self.assertEqual(send.call_count, 1)
                self.assertEqual(response.formatted_response, ExtractedFact(company='ACME', value=12))
                self.assertEqual(cached.formatted_response, response.formatted_response)

    def test_invalid_global_json_response_is_not_cached(self):
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                self.model = create_completion(self.config, tokenizer=Mock(), cache=MemoryCache())
                for content in ('', 'not JSON', '[]', 'null'):
                    with self.subTest(content=content), self.transport(asynchronous, content) as send:
                        with self.assertRaises(ValueError):
                            self.call(asynchronous, messages='Summarize.', response_format_json_object=True)
                    self.assertEqual(send.call_count, 1)
                with self.transport(asynchronous, '{"points": []}') as send:
                    response = self.call(asynchronous, messages='Summarize.', response_format_json_object=True)
                    cached = self.call(asynchronous, messages='Summarize.', response_format_json_object=True)
                self.assertEqual(send.call_count, 1)
                self.assertEqual(response.content, '{"points": []}')
                self.assertEqual(cached.content, response.content)


if __name__ == '__main__':
    unittest.main()
