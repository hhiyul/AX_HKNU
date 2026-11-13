import json
import httpx
import pytest

from app.ai.p5.provider import OpenAIProvider
from app.ai.responses import ProviderError


@pytest.mark.asyncio
async def test_count_and_stream_use_same_input_and_terminal_event():
    requests = []
    async def handler(request):
        requests.append(request)
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 20})
        data = 'data: {"type":"response.output_text.delta","delta":"안녕"}\n\ndata: {"type":"response.completed","response":{"status":"completed","model":"gpt-6-luna"}}\n\n'
        return httpx.Response(200, text=data, headers={"content-type": "text/event-stream"})
    client = OpenAIProvider("sk-fake-test", httpx.MockTransport(handler))
    wire = {"model": "gpt-6-luna", "input": [{"role": "user", "content": "hello"}], "tools": []}
    assert await client.count(wire) == 20
    events = [e async for e in client.stream(wire)]
    assert events == [{"type": "delta", "text": "안녕"}, {"type": "completed"}]
    counted, generated = [json.loads(r.content) for r in requests]
    assert all(generated[k] == counted[k] for k in wire)
    assert generated["stream"] is True and generated["store"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("usage,expected", [
    ({"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}, True),
    (None, False),
    ({"input_tokens": True, "output_tokens": 30, "total_tokens": 31}, False),
    ({"input_tokens": 120, "output_tokens": 30, "total_tokens": 999}, False),
])
async def test_stream_usage_is_reported_only_when_available_and_valid(usage, expected):
    async def handler(request):
        event = {"type": "response.completed", "response": {"status": "completed", "model": "gpt-6-luna", "usage": usage}}
        return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n", headers={"content-type": "text/event-stream"})
    client = OpenAIProvider("sk-fake-test", httpx.MockTransport(handler))
    events = [e async for e in client.stream({"model": "gpt-6-luna", "input": [], "tools": []})]
    assert events[-1] == {"type": "completed"}
    assert (len(events) == 2) is expected
    if expected:
        assert events[0] == {"type": "usage", **usage}


@pytest.mark.asyncio
async def test_summary_and_search_return_usage_without_injecting_it_into_text_or_tool_result():
    async def handler(request):
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 20})
        body = json.loads(request.content)
        payload = {"id": "resp-test", "status": "completed", "model": "gpt-6-luna", "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "result"}]}
        ], "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}}
        if body.get('tools'):
            assert body['tool_choice'] == 'required'
            payload['output'].insert(0, {'type': 'web_search_call', 'status': 'completed', 'action': {'type': 'search'}})
        return httpx.Response(200, json=payload)
    client = OpenAIProvider("sk-fake-test", httpx.MockTransport(handler))
    summary = await client.summarize([{"role": "user", "content": "old"}])
    assert summary == "result" and summary.usage["total_tokens"] == 25
    search = await client.web_search("public")
    assert search.usage["total_tokens"] == 25
    assert json.loads(json.dumps(search)) == {"text": "result", "urls": []}


@pytest.mark.asyncio
@pytest.mark.parametrize('call', [None, {'type': 'web_search_call', 'status': 'failed', 'action': {'type': 'search'}}, {'type': 'web_search_call', 'status': 'completed', 'action': {'type': 'open_page'}}])
async def test_search_does_not_claim_success_without_completed_search_action(call):
    usage = {'input_tokens': 20, 'output_tokens': 5, 'total_tokens': 25}
    async def handler(request):
        if request.url.path.endswith('input_tokens'):
            return httpx.Response(200, json={'object': 'response.input_tokens', 'input_tokens': 20})
        output = ([call] if call else []) + [{'type': 'message', 'content': [{'type': 'output_text', 'text': '확인되지 않은 주장'}]}]
        return httpx.Response(200, json={'status': 'completed', 'model': 'gpt-6-luna', 'output': output, 'usage': usage})
    with pytest.raises(ProviderError, match='SEARCH_NOT_PERFORMED') as caught:
        await OpenAIProvider('sk-fake-test', httpx.MockTransport(handler)).web_search('공개 검색어')
    assert caught.value.usage == usage


@pytest.mark.asyncio
async def test_search_preserves_inline_citation_positions_and_rejects_unsafe_urls():
    async def handler(request):
        if request.url.path.endswith('input_tokens'):
            return httpx.Response(200, json={'object': 'response.input_tokens', 'input_tokens': 20})
        output = [
            {'type':'web_search_call','status':'completed','action':{'type':'search'}},
            {'type':'message','content':[{'type':'output_text','text':'첫 주장입니다. 둘째 주장입니다.', 'annotations':[
                {'type':'url_citation','start_index':0,'end_index':7,'title':'공식 자료','url':'https://example.com/first'},
                {'type':'url_citation','start_index':999,'end_index':1000,'title':'범위 미확인','url':'https://example.com/second'},
                {'type':'url_citation','start_index':0,'end_index':7,'title':'위험','url':'javascript:alert(1)'},
            ]}]},
        ]
        return httpx.Response(200, json={'status':'completed','model':'gpt-6-luna','output':output})
    result = await OpenAIProvider('sk-fake-test', httpx.MockTransport(handler)).web_search('공개 검색어')
    assert '첫 주장입니다 [공식 자료](https://example.com/first).' in result['text']
    assert '위치가 확인되지 않은 출처' in result['text']
    assert result['urls'] == ['https://example.com/first', 'https://example.com/second']


@pytest.mark.asyncio
async def test_failed_response_keeps_reported_usage_without_claiming_success():
    usage = {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}
    async def handler(request):
        if request.url.path.endswith("input_tokens"):
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 20})
        if json.loads(request.content).get("stream"):
            event = {"type": "response.incomplete", "response": {"usage": usage}}
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n")
        return httpx.Response(200, json={"status": "incomplete", "usage": usage})
    client = OpenAIProvider("sk-fake-test", httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as failure:
        await client.summarize([{"role": "user", "content": "old"}])
    assert failure.value.usage == usage
    seen = []
    with pytest.raises(ProviderError):
        async for event in client.stream({"model": "gpt-6-luna", "input": [], "tools": []}): seen.append(event)
    assert seen == [{"type": "usage", **usage}]
