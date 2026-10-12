"""Provider contracts without spending live AI quota."""
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault('DATABASE_URL', 'postgresql://user:pass@localhost/test')

import httpx
import pytest
from fastapi import HTTPException
from modules.siabumdes.application import riska_ai, riska_agent_provider


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
    content = 'Siap membantu.'
    async def post(client, url, **kwargs):
        payload = kwargs['json']
        assert payload['model'] == model
        if provider == 'sol':
            assert url == 'https://api.openai.com/v1/responses'
            assert payload['tools'][0]['name'] == 'daftar_unit'
            assert payload['store'] is False and payload['max_output_tokens'] == 2400
            assert payload['parallel_tool_calls'] is False
            raw = {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': content}]}]}
        else:
            assert url == 'https://api.anthropic.com/v1/messages'
            assert kwargs['headers']['anthropic-version'] == '2023-06-01'
            assert payload['max_tokens'] == 2400
            raw = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': content}]}
        raw['usage'] = {'input_tokens': 100, 'output_tokens': 20}
        return httpx.Response(503 if failure else 200, json=raw, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    user = SimpleNamespace(id='test', role='admin', name='Admin')
    if failure:
        with pytest.raises(HTTPException) as error:
            await riska_agent_provider.call(user, provider, [{'role': 'user', 'content': 'Halo'}], [{'name': 'daftar_unit', 'description': 'Unit', 'input_schema': {'type': 'object'}}], 'Riska')
        assert error.value.status_code == 503
        record.assert_awaited_once_with('test-call', 'failed', 0, 0)
    else:
        reply, usage = await riska_agent_provider.call(user, provider, [{'role': 'user', 'content': 'Halo'}], [{'name': 'daftar_unit', 'description': 'Unit', 'input_schema': {'type': 'object'}}], 'Riska')
        assert reply[0]['text'] == 'Siap membantu.' and usage['provider'] == provider
        record.assert_awaited_once_with('test-call', 'succeeded', 100, 20)
    reserve.assert_awaited_once_with('test', model)


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['sol', 'sonnet'])
async def test_native_tool_roundtrip_and_incomplete_response_is_rejected(provider, monkeypatch):
    import json
    key, model_var, model = riska_ai.MODELS[provider]
    monkeypatch.setenv(key, 'test-placeholder')
    monkeypatch.setenv(model_var, model)
    monkeypatch.setenv('RISKA_AI_ENABLED', 'true')
    monkeypatch.setattr(riska_ai, 'reserve', AsyncMock(return_value='tool-call'))
    record = AsyncMock()
    monkeypatch.setattr(riska_ai, 'record', record)
    requests = []
    async def post(client, url, **kwargs):
        requests.append(kwargs['json'])
        usage = {'input_tokens': 20, 'output_tokens': 10}
        if provider == 'sol':
            raw = {'status': 'completed' if len(requests) == 1 else 'incomplete', 'usage': usage,
                   'output': [{'type': 'function_call', 'call_id': 'report-1', 'name': 'baca_laporan', 'arguments': json.dumps({'report': 'profit_loss', 'last_months': 6})}]}
        else:
            raw = {'stop_reason': 'tool_use' if len(requests) == 1 else 'max_tokens', 'usage': usage,
                   'content': [{'type': 'tool_use', 'id': 'report-1', 'name': 'baca_laporan', 'input': {'report': 'profit_loss', 'last_months': 6}}]}
        return httpx.Response(200, json=raw, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    user = SimpleNamespace(id='test', role='admin', name='Admin')
    messages = [{'role': 'user', 'content': 'laporan enam bulan terakhir'}]
    definitions = [{'name': 'baca_laporan', 'description': 'Laporan', 'input_schema': {'type': 'object'}}]
    blocks, _ = await riska_agent_provider.call(user, provider, messages, definitions, 'Riska')
    assert blocks[0]['input']['last_months'] == 6
    messages.extend([{'role': 'assistant', 'content': blocks}, {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'report-1', 'content': '{"rows": []}'}]}])
    with pytest.raises(HTTPException) as error:
        await riska_agent_provider.call(user, provider, messages, definitions, 'Riska')
    assert error.value.status_code == 503
    if provider == 'sol':
        assert requests[1]['input'][-1]['type'] == 'function_call_output'
        assert requests[1]['input'][-1]['call_id'] == 'report-1'
    else:
        assert requests[1]['messages'][-1]['content'][0]['tool_use_id'] == 'report-1'
    record.assert_awaited_with('tool-call', 'failed', 20, 10)
