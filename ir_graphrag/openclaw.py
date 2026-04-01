"""Transport escaping for gateways that strip angle-bracket graph delimiters.

Registered through GraphRAG's public model factory; upstream graph code is unchanged.
Only the marked extraction prompt uses this encoding. All other calls pass through.
"""
import re
from graphrag_llm.completion.lite_llm_completion import LiteLLMCompletion

MARKER = 'IR_GRAPH_EXTRACTION_V1'


def encode_messages(messages):
    if isinstance(messages, str):
        messages = [{'role': 'user', 'content': messages}]
    graph = any(MARKER in str(m.get('content', '')) for m in messages)
    if not graph:
        return messages, False
    encoded = []
    for message in messages:
        content = message.get('content')
        if isinstance(content, str):
            content = content.replace('<|>', '|||').replace('<|COMPLETE|>', 'END_OF_GRAPH')
            content = re.sub(r'<([a-z_]+)>', r'\1', content)
        encoded.append({**message, 'content': content})
    return encoded, True


def decode_response(response, graph):
    if graph:
        response.choices[0].message.content = response.content.replace('|||', '<|>').replace('END_OF_GRAPH', '<|COMPLETE|>')
    return response


class OpenClawCompletion(LiteLLMCompletion):
    def completion(self, **kwargs):
        kwargs['messages'], graph = encode_messages(kwargs['messages'])
        return decode_response(super().completion(**kwargs), graph)

    async def completion_async(self, **kwargs):
        kwargs['messages'], graph = encode_messages(kwargs['messages'])
        return decode_response(await super().completion_async(**kwargs), graph)


def register():
    from graphrag_llm.completion.completion_factory import register_completion
    register_completion('ir_openclaw', OpenClawCompletion)
