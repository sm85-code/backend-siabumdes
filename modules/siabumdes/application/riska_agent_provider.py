"""ERP-style native tool protocol, bounded and without automatic retries."""
import json
import os

import httpx
from fastapi import HTTPException
from . import riska_ai


def openai_input(messages):
    items = []
    for message in messages:
        content = message['content']
        if isinstance(content, str):
            items.append(message)
            continue
        for block in content:
            if block['type'] == 'text':
                items.append({'role': message['role'], 'content': block['text']})
            elif block['type'] == 'tool_use':
                items.append({'type': 'function_call', 'call_id': block['id'], 'name': block['name'], 'arguments': json.dumps(block['input'])})
            elif block['type'] == 'tool_result':
                items.append({'type': 'function_call_output', 'call_id': block['tool_use_id'], 'output': block['content']})
            elif block['type'] == 'reasoning':
                items.append(block['item'])
    return items


async def call(user, provider, messages, definitions, system):
    if provider not in riska_ai.MODELS or not riska_ai.ready(provider):
        raise HTTPException(503, 'Agen AI belum tersedia. Pilih pintasan atau mode Internal untuk perintah yang sudah jelas.')
    key_var, model_var, default = riska_ai.MODELS[provider]
    model = os.getenv(model_var, default)
    if model not in riska_ai.ALLOWED[provider]:
        raise HTTPException(503, 'Model agen belum didukung konfigurasi biaya.')
    if len(json.dumps(messages, default=str).encode()) > 60000:
        raise HTTPException(422, 'Konteks terlalu panjang. Mulai percakapan baru atau persempit laporan.')
    ident = await riska_ai.reserve(user.id, model)
    incoming = outgoing = 0
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            if provider == 'sol':
                payload = {'model': model, 'instructions': system, 'input': openai_input(messages),
                           'tools': [{'type': 'function', 'name': d['name'], 'description': d['description'], 'parameters': d['input_schema'], 'strict': False} for d in definitions],
                           'max_output_tokens': 2400, 'reasoning': {'effort': 'low'}, 'store': False,
                           'include': ['reasoning.encrypted_content'], 'parallel_tool_calls': False}
                response = await client.post('https://api.openai.com/v1/responses', headers={'Authorization': 'Bearer ' + os.getenv(key_var, '')}, json=payload)
            else:
                response = await client.post('https://api.anthropic.com/v1/messages', headers={'x-api-key': os.getenv(key_var, ''), 'anthropic-version': '2023-06-01'},
                                             json={'model': model, 'system': system, 'messages': messages, 'tools': definitions, 'max_tokens': 2400})
            response.raise_for_status()
            raw = response.json()
        usage = raw.get('usage', {})
        incoming, outgoing = usage.get('input_tokens'), usage.get('output_tokens')
        if type(incoming) is not int or type(outgoing) is not int or incoming < 0 or outgoing < 0:
            incoming = outgoing = 0
            raise ValueError('Invalid usage')
        blocks = []
        if provider == 'sol':
            if raw.get('status') != 'completed' or raw.get('error'):
                raise ValueError('Incomplete response')
            for item in raw.get('output', []):
                if item['type'] == 'message':
                    blocks.extend({'type': 'text', 'text': p.get('text', p.get('refusal', ''))} for p in item.get('content', []) if p['type'] in {'output_text', 'refusal'})
                elif item['type'] == 'function_call':
                    blocks.append({'type': 'tool_use', 'id': item['call_id'], 'name': item['name'], 'input': json.loads(item['arguments'])})
                elif item['type'] == 'reasoning':
                    blocks.append({'type': 'reasoning', 'item': item})
        else:
            if raw.get('stop_reason') not in {'end_turn', 'tool_use'}:
                raise ValueError('Incomplete response')
            blocks = [b for b in raw.get('content', []) if b['type'] in {'text', 'tool_use'}]
        if not blocks:
            raise ValueError('Empty response')
        await riska_ai.record(ident, 'succeeded', incoming, outgoing)
        return blocks, {'provider': provider, 'model': model, 'input_tokens': incoming, 'output_tokens': outgoing}
    except Exception:
        await riska_ai.record(ident, 'failed', incoming, outgoing)
        raise HTTPException(503, 'Panggilan agen AI gagal. Tidak ada eksekusi transaksi otomatis; periksa laporan atau draf yang sudah tersedia.') from None
