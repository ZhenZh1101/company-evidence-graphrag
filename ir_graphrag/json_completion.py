"""Use JSON mode for chat APIs without native JSON Schema responses."""
import json

from graphrag_llm.completion import register_completion
from graphrag_llm.completion.lite_llm_completion import LiteLLMCompletion
from graphrag_llm.middleware import with_cache
from graphrag_llm.utils import structure_completion_response


def _json_arguments(kwargs):
    schema = kwargs.pop('response_format', None)
    json_object = kwargs.pop('response_format_json_object', False)
    if schema is None and not json_object:
        return kwargs

    instruction = 'Return only a valid JSON object, without Markdown fences.'
    if schema is not None:
        instruction += '\nThe JSON object must match this JSON Schema:\n' + json.dumps(
            schema.model_json_schema(), ensure_ascii=False
        )
    kwargs['messages'] = [
        {'role': 'system', 'content': instruction}, *kwargs['messages']
    ]
    kwargs['response_format'] = {'type': 'json_object'}
    return kwargs


def _validate_response(response, request):
    schema = request.get('response_format')
    if schema is not None:
        structure_completion_response(response.content, schema)
    elif request.get('response_format_json_object') and not request.get('stream'):
        if not isinstance(json.loads(response.content), dict):
            raise ValueError('Expected a JSON object from the chat model.')
    return response


class JsonCompletion(LiteLLMCompletion):
    def __init__(self, **kwargs):
        cache = kwargs.pop('cache', None)
        super().__init__(cache=None, **kwargs)
        completion = self._completion
        completion_async = self._completion_async

        # Validate before caching: JSON mode guarantees syntax, not the schema.
        # Public methods still construct the original typed response and metrics.
        def json_completion(**request):
            response = completion(**_json_arguments(request.copy()))
            return _validate_response(response, request)

        async def json_completion_async(**request):
            response = await completion_async(**_json_arguments(request.copy()))
            return _validate_response(response, request)

        self._completion = json_completion
        self._completion_async = json_completion_async
        if cache is not None:
            self._completion, self._completion_async = with_cache(
                sync_middleware=self._completion,
                async_middleware=self._completion_async,
                request_type='chat', cache=cache,
                cache_key_creator=lambda request: self._cache_key_creator(
                    _json_arguments(request.copy())
                ),
            )


def register():
    register_completion('ir_json_chat', JsonCompletion)
