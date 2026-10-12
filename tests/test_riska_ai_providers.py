"""Provider contracts without spending live AI quota."""
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault('DATABASE_URL', 'postgresql://user:pass@localhost/test')

import httpx
import pytest
from fastapi import HTTPException
from modules.siabumdes.application import riska_ai


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['sol', 'sonnet'])
@pytest.mark.parametrize('failure', [False, True])
async def test_provider_request_response_and_usage(provider, failure, monkeypatch):
    key_var, model_var, model = riska_ai.MODELS[provider]
    monkeypatch.setenv(key_var, 'test-placeholder')
    monkeypatch.setenv(model_var, model)
    monkeypatch.setenv('RISKA_AI_ENABLED', 'true')
    reserve = AsyncMock(return_value='test-call')
    record = AsyncMock()
    monkeypatch.setattr(riska_ai, 'reserve', reserve)
    monkeypatch.setattr(riska_ai, 'record', record)
    content = json.dumps({'action': 'explain', 'command': '', 'reply': 'Siap membantu.', 'task': None})
    async def post(client, url, **kwargs):
        payload = kwargs['json']
        assert payload['model'] == model
        if provider == 'sol':
            assert url == 'https://api.openai.com/v1/responses'
            schema = payload['text']['format']['schema']
            assert set(schema['required']) == set(schema['properties'])
            assert payload['store'] is False and payload['max_output_tokens'] == 1200
            raw = {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': content}]}]}
        else:
            assert url == 'https://api.anthropic.com/v1/messages'
            assert kwargs['headers']['anthropic-version'] == '2023-06-01'
            assert payload['max_tokens'] == 1200
            raw = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': content}]}
        raw['usage'] = {'input_tokens': 100, 'output_tokens': 20}
        return httpx.Response(503 if failure else 200, json=raw, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    user = SimpleNamespace(id='test', role='admin', name='Admin')
    if failure:
        with pytest.raises(HTTPException) as error:
            await riska_ai.interpret(user, 'Halo', provider, {})
        assert error.value.status_code == 503
        record.assert_awaited_once_with('test-call', 'failed')
    else:
        reply, usage = await riska_ai.interpret(user, 'Halo', provider, {})
        assert reply.reply == 'Siap membantu.' and usage['provider'] == provider
        record.assert_awaited_once_with('test-call', 'succeeded', 100, 20)
    reserve.assert_awaited_once_with('test', model)
