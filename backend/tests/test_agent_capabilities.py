"""AG-P1~5: 실제 API/격리 MongoDB와 모의 모델의 입력·업무 결과를 검증한다."""
import asyncio
import json

import pytest
from bson import ObjectId

from ai_fakes import COMPLETED, FakeProvider, answer, tool_call
from app.ai.events import RunEvents
from app.ai.runtime import Runtime, set_runtime
from app.schemas.ai_scope import KINDS
from app.services.store import utcnow


@pytest.fixture
async def workspace(client, login):
    await login(client)
    project = (await client.post('/projects', json={'name': 'AG 검증'})).json()
    base = f"/projects/{project['id']}"
    conversation = (await client.post(base + '/conversations', json={})).json()
    return client, base, project['id'], conversation['id']


@pytest.fixture
def model():
    def install(turns):
        provider = FakeProvider(turns)
        runtime = Runtime(provider, RunEvents(), {}, 'gpt-6-luna')
        set_runtime(runtime)
        return provider, runtime
    yield install
    set_runtime(None)


async def send(workspace, runtime, text='질문', **extra):
    client, base, _, cid = workspace
    response = await client.post(base + '/runs', json={'text': text, 'conversation_id': cid, **extra})
    assert response.status_code == 202, response.text
    rid = response.json()['run_id']
    await asyncio.wait_for(runtime.tasks[rid], 10)
    result = (await client.get(base + '/runs/' + rid)).json()
    assert result['status'] == 'completed', result
    return rid


async def test_scope_filters_previous_answers_and_summary_without_deleting_them(workspace, model, db):
    client, base, pid, cid = workspace
    await client.put(base + '/items/plan/ck_01', json={'content': 'EXCLUDED_FACT', 'revision': 1})
    provider, runtime = model([answer('EXCLUDED_FACT에서 유래한 답변'), answer('제외 후 답변')])
    await send(workspace, runtime)
    messages = await db.messages.find({'conversation_id': ObjectId(cid)}).to_list()
    await db.projects.update_one({'_id': ObjectId(pid)}, {'$set': {f'chat_capsules.{cid}': {
        'text': 'EXCLUDED_FACT 요약', 'covered_ids': [str(m['_id']) for m in messages],
        'source_ids': ['ck_01'], 'revision': 1}}})
    response = await client.put(base + f'/conversations/{cid}/scope', json={
        'scope': {'kinds': KINDS, 'excluded': ['ck_01']}, 'revision': 0})
    assert response.status_code == 200, response.text
    await send(workspace, runtime, '다음 요청')
    assert 'EXCLUDED_FACT' not in json.dumps(provider.calls[-1]['input'], ensure_ascii=False)
    assert await db.messages.count_documents({'content': 'EXCLUDED_FACT에서 유래한 답변'}) == 1


async def test_scope_cas_empty_scope_and_other_project(workspace, model):
    client, base, _, cid = workspace
    path = base + f'/conversations/{cid}/scope'
    body = {'scope': {'kinds': [], 'excluded': []}, 'revision': 0}
    assert (await client.put(path, json=body)).status_code == 200
    assert (await client.put(path, json=body)).status_code == 409
    provider, runtime = model([answer('내 질문으로 계속')])
    await send(workspace, runtime, '외부 참고 없이 계속')
    assert 'AG 검증' not in json.dumps(provider.calls[-1]['input'], ensure_ascii=False)
    other = (await client.post('/projects', json={'name': '다른 프로젝트'})).json()['id']
    assert (await client.get(f'/projects/{other}/conversations/{cid}/scope')).status_code == 404


async def test_task_tool_reads_beyond_snapshot_and_never_writes(workspace, model, db):
    client, base, pid, _ = workspace
    for day in range(1, 32):
        await client.post(base + '/tasks', json={'title': f'과거 {day}', 'date': f'2026-01-{day:02}'})
    target = (await client.post(base + '/tasks', json={'title': '기한 밖에서 찾을 일정', 'date': '2026-12-03'})).json()
    provider, runtime = model([[tool_call('query_tasks', {'date_from': '2026-12-01', 'date_to': '2026-12-07', 'done': False, 'offset': 0}), COMPLETED], answer('일정을 찾았습니다')])
    await send(workspace, runtime)
    output = json.loads(provider.calls[-1]['input'][-1]['output'])
    assert [t['id'] for t in output['items']] == [target['id']]
    assert output['date_basis'] == 'scheduled_date'
    assert await db.tasks.count_documents({'project_id': ObjectId(pid)}) == 32


async def test_search_paginates_and_reads_original_not_snapshot(workspace, model):
    client, base, _, _ = workspace
    for i in range(23):
        response = await client.post(base + '/items/record', json={'title': f'기록 {i}', 'content': f'원문 {i}'})
        assert response.status_code == 201, response.text
    first = (await client.get(base + '/ai-sources', params={'query': '기록'})).json()
    assert first['status'] == 'partial' and len(first['items']) == 20
    second = (await client.get(base + '/ai-sources', params={'query': '기록', 'offset': first['next_offset']})).json()
    assert len(second['items']) == 3
    key = first['items'][0]['key']
    provider, runtime = model([[tool_call('read_source', {'key': key, 'offset': 0}), COMPLETED], answer('원문 확인')])
    await send(workspace, runtime)
    result = json.loads(provider.calls[-1]['input'][-1]['output'])
    assert result['status'] == 'read' and result['content'] == '원문 0'


async def test_excluded_task_cannot_be_read_by_id(workspace, model):
    client, base, _, cid = workspace
    task = (await client.post(base + '/tasks', json={'title': '숨길 일정', 'date': '2026-12-03'})).json()
    key = 'task:' + task['id']
    await client.put(base + f'/conversations/{cid}/scope', json={'scope': {'kinds': KINDS, 'excluded': [key]}, 'revision': 0})
    provider, runtime = model([[tool_call('read_source', {'key': key, 'offset': 0}), COMPLETED], answer('사용할 수 없음')])
    await send(workspace, runtime)
    assert '숨길 일정' not in json.dumps(provider.calls, ensure_ascii=False)
    assert json.loads(provider.calls[-1]['input'][-1]['output'])['status'] == 'unavailable'


async def candidates(workspace, model):
    client, base, _, cid = workspace
    provider, runtime = model([[tool_call('suggest_tasks', {'tasks': [
        {'title': '공방 확인', 'date': None, 'note': '납기 확인'},
        {'title': '사진 촬영', 'date': '2027-01-08', 'note': ''}]}), COMPLETED], answer('내용과 날짜를 확인해 주세요')])
    await send(workspace, runtime, '할 일로 정리해 줘')
    message = (await client.get(base + '/messages', params={'conversation_id': cid})).json()[-1]
    return message, provider


async def test_chat_tasks_require_confirmation_and_register_once(workspace, model, db):
    client, base, pid, cid = workspace
    message, _ = await candidates(workspace, model)
    assert await db.tasks.count_documents({'project_id': ObjectId(pid)}) == 0
    proposed = message['proposals']
    payload = {'request_id': 'confirmed-1', 'tasks': [
        {'source_proposal_id': proposed[0]['proposal_id'], 'title': '사용자가 수정한 내용', 'date': '2027-01-09', 'purpose_ids': []},
        {'source_proposal_id': proposed[1]['proposal_id'], 'title': '사진 촬영', 'date': '2027-01-10', 'purpose_ids': ['funding', 'business_plan']}]}
    responses = await asyncio.gather(*[client.post(base + '/tasks/registrations', json=payload) for _ in range(2)])
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    result = (await client.get(base + '/tasks/registrations/confirmed-1')).json()
    assert all(row['status'] in ['registered', 'already_linked'] for row in result['results'])
    saved = await db.tasks.find({'project_id': ObjectId(pid)}).sort('title', 1).to_list()
    assert len(saved) == 2 and all(t['source_conversation_id'] == ObjectId(cid) for t in saved)
    assert {t['title'] for t in saved} == {'사용자가 수정한 내용', '사진 촬영'}
    assert all(not t['done'] for t in saved)
    payload['tasks'][0]['title'] = '같은 요청 내용을 바꿔 전송'
    assert (await client.post(base + '/tasks/registrations', json=payload)).status_code == 409


async def test_partial_registration_preserves_success_and_unknown_lookup(workspace, model, db):
    client, base, pid, _ = workspace
    message, _ = await candidates(workspace, model)
    proposal = message['proposals'][0]
    response = await client.post(base + '/tasks/registrations', json={'request_id': 'partial-1', 'tasks': [
        {'source_proposal_id': proposal['proposal_id'], 'title': '확인', 'date': '2027-01-01'},
        {'source_proposal_id': str(ObjectId()), 'title': '없는 후보', 'date': '2027-01-01'}]})
    assert response.status_code == 200, response.text
    assert [row['status'] for row in response.json()['results']] == ['registered', 'failed']
    # 저장 뒤 응답/결과 기록 유실을 모의한다. 조회는 일정 하나를 찾고 새 쓰기를 하지 않는다.
    await db.task_registrations.update_one({'project_id': ObjectId(pid)}, {'$set': {'results.0': {'proposal_id': proposal['proposal_id'], 'status': 'unknown'}}})
    resolved = (await client.get(base + '/tasks/registrations/partial-1')).json()
    assert resolved['results'][0]['status'] == 'already_linked'
    assert await db.tasks.count_documents({'project_id': ObjectId(pid)}) == 1


async def test_deleted_source_no_longer_reenters_history(workspace, model, db):
    client, base, _, _ = workspace
    record = (await client.post(base + '/items/record', json={'title': '기록', 'content': 'REMOVED_SOURCE'})).json()
    provider, runtime = model([answer('REMOVED_SOURCE에서 나온 답'), answer('새 답')])
    await send(workspace, runtime)
    await db.items.delete_one({'key': record['key']})
    await send(workspace, runtime, '다음 질문')
    assert 'REMOVED_SOURCE' not in json.dumps(provider.calls[-1]['input'])


async def make_doc(workspace, **selection):
    client, base, _, _ = workspace
    result = await client.post(base + '/docs', json={'template_id': 'funding', **selection})
    assert result.status_code == 201, result.text
    return result.json(), base + '/docs/' + result.json()['id']


async def assist_request(client, url, runtime, action='draft', **payload):
    response = await client.post(url + '/assist', json={'action': action, **payload})
    assert response.status_code == 202, response.text
    rid = response.json()['run_id']
    await asyncio.wait_for(runtime.tasks[rid], 10)
    return rid


async def test_item_history_saved_siblings_and_selection_are_isolated(workspace, model):
    client, base, _, _ = workspace
    _, url = await make_doc(workspace)
    await client.put(url + '/sections/F01', json={'text': 'SAVED_SIBLING', 'revision': 0})
    await client.put(url + '/sections/F01/draft', json={'text': 'OTHER_UNSAVED', 'draft_revision': 0})
    await client.put(base + '/items/plan/ck_01', json={'content': 'PICKED_PLAN', 'revision': 1})
    item = url + '/sections/F05'
    save = await client.put(item + '/assist-context', json={'references': [{'type': 'plan', 'id': 'ck_01'}], 'scope': {}, 'revision': 0})
    assert save.status_code == 200, save.text
    provider, runtime = model([answer('ITEM_FIRST_ANSWER'), answer('OTHER_ITEM_ANSWER'), answer('ITEM_SECOND_ANSWER')])
    await assist_request(client, item, runtime, request='FIRST_ITEM_REQUEST')
    await assist_request(client, url + '/sections/F02', runtime, request='OTHER_ITEM_REQUEST')
    await assist_request(client, item, runtime, request='SECOND_ITEM_REQUEST')
    sent = json.dumps(provider.calls[-1]['input'], ensure_ascii=False)
    assert all(word in sent for word in ['SAVED_SIBLING', 'PICKED_PLAN', 'FIRST_ITEM_REQUEST', 'ITEM_FIRST_ANSWER'])
    assert all(word not in sent for word in ['OTHER_UNSAVED', 'OTHER_ITEM_REQUEST', 'OTHER_ITEM_ANSWER'])
    assert (await client.get(url + '/sections/F02/assist-context')).json()['references'] == []
    # Removing a source also removes mixed prior requests/answers from subsequent input.
    await client.put(item + '/assist-context', json={'references': [], 'scope': {'kinds': []}, 'revision': 1})
    provider, runtime = model([answer('범위를 변경한 답')])
    await assist_request(client, item, runtime)
    sent = json.dumps(provider.calls[-1]['input'], ensure_ascii=False)
    assert 'SAVED_SIBLING' in sent and 'PICKED_PLAN' not in sent and 'ITEM_FIRST_ANSWER' not in sent


async def test_handoff_uses_one_removable_answer_and_no_whole_chat(workspace, model, db):
    client, base, pid, cid = workspace
    provider, runtime = model([answer('SELECTED_ANSWER'), answer('UNSELECTED_ANSWER')])
    await send(workspace, runtime)
    first = (await client.get(base + '/messages', params={'conversation_id': cid})).json()[-1]
    await send(workspace, runtime, 'OTHER_CONVERSATION_INPUT')
    doc, url = await make_doc(workspace, origin_message_id=first['id'])
    assert doc['origin_conversation_id'] == cid
    assert all(section['text'] == '' for section in doc['sections'])
    assert (await client.get(url + '/sections/F01/assist-context')).json()['references'] == [{'type': 'message', 'id': first['id']}]
    assert (await client.get(url + '/sections/F02/assist-context')).json()['references'] == []
    provider, runtime = model([answer('문서 후보')])
    await assist_request(client, url + '/sections/F01', runtime)
    sent = json.dumps(provider.calls[-1]['input'], ensure_ascii=False)
    assert 'SELECTED_ANSWER' in sent and 'UNSELECTED_ANSWER' not in sent and 'OTHER_CONVERSATION_INPUT' not in sent
    await client.put(url + '/sections/F01/assist-context', json={'references': [], 'scope': {'kinds': []}, 'revision': 0})
    provider, runtime = model([answer('참고 해제 후')])
    await assist_request(client, url + '/sections/F01', runtime)
    assert 'SELECTED_ANSWER' not in json.dumps(provider.calls[-1]['input'])
    assert await db.structured_docs.count_documents({'project_id': ObjectId(pid)}) == 1


async def test_catalog_product_registration_snapshot_and_initial_quantity_once(workspace, model):
    from test_assets import PNG
    client, base, pid, _ = workspace
    draft = (await client.post(base + '/products', json={'name': '초안'})).json()
    invalid = await client.post(base + '/docs', json={'template_id': 'funding', 'product_ids': [draft['id']]})
    assert invalid.status_code == 422
    photo = (await client.post('/assets', data={'project_id': pid, 'asset_type': 'product_image'}, files={'file': ('photo.png', PNG, 'image/png')})).json()
    body = {'name': '작품', 'intent': '선물용', 'material': '은', 'process': '주조', 'photo_ids': [photo['id']],
            'cost': None, 'sale_price': 30000, 'price_estimated': True, 'quantity_date': '2026-11-26',
            'options': [{'name': '기본', 'size': 'M', 'color': '은색', 'sale_price': 0, 'initial_quantity': 3, 'produced_quantity': 2, 'sold_quantity': 1, 'replenishment_threshold': 1}], 'status': 'registered'}
    response = await client.post(base + '/products', json=body)
    assert response.status_code == 201, response.text
    product = response.json()
    assert product['options'][0]['inventory'] == 4
    doc, url = await make_doc(workspace, product_ids=[product['id']])
    assert doc['product_snapshots'][0]['sale_price'] == 30000 and doc['product_snapshots'][0]['price_estimated']
    changed = await client.put(base + '/products/' + product['id'], json={**body, 'sale_price': 35000, 'revision': 1})
    assert changed.status_code == 200, changed.text
    assert changed.json()['options'][0]['inventory'] == 4
    altered = {**body, 'revision': 2, 'options': [{**body['options'][0], 'initial_quantity': 4}]}
    assert (await client.put(base + '/products/' + product['id'], json=altered)).status_code == 409
    assert (await client.get(url)).json()['product_snapshots'][0]['sale_price'] == 30000
    check = (await client.get(url + '/inspection')).json()
    assert 'SOURCE_CHANGED' in [i['code'] for i in check['issues']]
    provider, runtime = model([answer('선택 제품으로 후보')])
    await assist_request(client, url + '/sections/F01', runtime)
    assert '30000' in json.dumps(provider.calls[-1]['input'])
    assert '35000' not in json.dumps(provider.calls[-1]['input'])


async def test_contacts_scope_and_model_owner_read(workspace, model):
    client, base, _, _ = workspace
    a = await client.post(base + '/contacts', json={'name': '지역 공방', 'note': '예약은 전화로', 'scope': 'project'})
    assert a.status_code == 201, a.text
    key = 'contact:' + a.json()['id']
    provider, runtime = model([[tool_call('read_source', {'key': key, 'offset': 0}), COMPLETED], answer('공방 확인')])
    await send(workspace, runtime)
    assert '예약은 전화로' in json.dumps(provider.calls[-1]['input'], ensure_ascii=False)
    other = (await client.post('/projects', json={'name': '다른 프로젝트'})).json()
    result = (await client.get(f"/projects/{other['id']}/ai-sources/read", params={'key': key})).json()
    assert result['status'] == 'unavailable'
    assert (await client.get(f"/projects/{other['id']}/contacts")).json() == []


async def test_default_inspection_is_model_free_and_ai_check_cannot_apply(workspace, model):
    client, _, _, _ = workspace
    _, url = await make_doc(workspace)
    await client.put(url + '/sections/F01', json={'text': '확인 필요: 포장 견적', 'revision': 0})
    provider, runtime = model([answer('포장 견적을 확인해 주세요.')])
    before = (await client.get(url)).json()
    inspection = (await client.get(url + '/inspection')).json()
    assert not provider.calls and not inspection['model_called']
    assert {'REQUIRED_EMPTY', 'UNCONFIRMED', 'UNREVIEWED'} <= {i['code'] for i in inspection['issues']}
    await assist_request(client, url + '/sections/F01', runtime, action='check')
    message = (await client.get(url + '/sections/F01/messages')).json()[-1]
    assert message['assist_action'] == 'check' and message['candidate'] is None
    apply = await client.post(url + '/sections/F01/apply', json={'message_id': message['id'], 'draft_revision': 0})
    assert apply.status_code == 422
    after = (await client.get(url)).json()
    assert before['sections'] == after['sections'] and after['sections'][0]['state'] == 'WRITING'


async def test_continuation_is_new_run_preserves_partial_and_rechecks_scope(workspace, model, db):
    client, base, pid, cid = workspace
    provider, runtime = model([answer('ORIGINAL_PARTIAL'), answer('REMAINING')])
    rid = await send(workspace, runtime, 'ORIGINAL_REQUEST')
    run = await db.ai_runs.find_one({'_id': ObjectId(rid)})
    mid = run['assistant_message_id']
    await db.ai_runs.update_one({'_id': run['_id']}, {'$set': {'status': 'stopped'}})
    await db.messages.update_one({'_id': mid}, {'$set': {'completion': 'PARTIAL'}})
    new_rid = await send(workspace, runtime, continuation_of=str(mid))
    assert new_rid != rid
    sent = json.dumps(provider.calls[-1]['input'], ensure_ascii=False)
    assert 'ORIGINAL_REQUEST' in sent and 'accepted_partial' in sent and 'remaining work' in sent
    original = await db.messages.find_one({'_id': mid})
    assert original['content'] == 'ORIGINAL_PARTIAL' and original['completion'] == 'PARTIAL'
    fresh = await db.messages.find_one({'request_id': ObjectId(new_rid), 'role': 'ASSISTANT'})
    assert fresh['continuation_of'] == mid
    await client.put(base + f'/conversations/{cid}/scope', json={'scope': {'kinds': []}, 'revision': 0})
    denied = await client.post(base + '/runs', json={'text': '계속', 'conversation_id': cid, 'continuation_of': str(mid)})
    assert denied.status_code == 422
    await db.messages.update_one({'_id': mid}, {'$set': {'completion': 'UNKNOWN'}})
    assert (await client.post(base + '/runs', json={'text': '계속', 'conversation_id': cid, 'continuation_of': str(mid)})).status_code == 409


async def test_excluded_derived_record_not_recovered_by_search_or_exact_id(workspace, model):
    client, base, _, cid = workspace
    await client.put(base + '/items/plan/ck_01', json={'content': 'DERIVED_SECRET', 'revision': 1})
    _, runtime = model([answer('DERIVED_SECRET 요약')])
    await send(workspace, runtime)
    message = (await client.get(base + '/messages', params={'conversation_id': cid})).json()[-1]
    record = (await client.post(base + '/items/record', json={'message_id': message['id'], 'title': '기록', 'content': message['content']})).json()
    await client.put(base + f'/conversations/{cid}/scope', json={'scope': {'kinds': KINDS, 'excluded': ['ck_01']}, 'revision': 0})
    key = 'record:' + record['key']
    response = (await client.get(base + '/ai-sources/read', params={'key': key, 'conversation_id': cid})).json()
    assert response['status'] == 'unavailable'
    provider, runtime = model([answer('새 범위')])
    await send(workspace, runtime)
    assert 'DERIVED_SECRET' not in json.dumps(provider.calls[-1]['input'])


async def test_selected_file_reads_original_and_reports_unsupported(workspace, model, monkeypatch):
    from app.config import Settings
    from app.services import source_reader
    monkeypatch.setattr(source_reader, 'get_settings', lambda: Settings(ai_read_attachments=True))
    client, _, pid, _ = workspace
    asset = (await client.post('/assets', data={'project_id': pid, 'asset_type': 'document'}, files={'file': ('quote.txt', b'FILE_ORIGINAL_CONTENT', 'text/plain')})).json()
    _, url = await make_doc(workspace)
    provider, runtime = model([answer('파일을 확인한 후보')])
    await assist_request(client, url + '/sections/F01', runtime, references=[{'type': 'file', 'id': asset['id']}])
    assert 'FILE_ORIGINAL_CONTENT' in json.dumps(provider.calls[-1]['input'])
    # The model cannot invent a target key to bypass the document-type exclusion.
    response = (await client.get(workspace[1] + '/ai-sources/read', params={'key': f"target:{url.split('/')[-1]}:F01"})).json()
    assert response['status'] == 'unavailable'


async def test_brand_common_sources_do_not_include_other_project_sources(workspace):
    client, base, pid, _ = workspace
    brand = (await client.post('/brands', json={'name': '공통 브랜드'})).json()
    project = (await client.get(base)).json()
    assert (await client.patch(base, json={'brand_id': brand['id'], 'revision': project['revision']})).status_code == 200
    shared = await client.post(base + '/contacts', json={'name': '공통 공방', 'scope': 'brand'})
    assert shared.status_code == 201, shared.text
    local = (await client.post(base + '/contacts', json={'name': '전용 공방'})).json()
    other = (await client.post('/projects', json={'name': '같은 브랜드의 다른 기획', 'brand_id': brand['id']})).json()
    other_base = '/projects/' + other['id']
    assert [row['id'] for row in (await client.get(other_base + '/contacts')).json()] == [shared.json()['id']]
    result = (await client.get(other_base + '/ai-sources/read', params={'key': 'contact:' + local['id']})).json()
    assert result['status'] == 'unavailable'
    asset = (await client.post('/assets', data={'brand_id': brand['id'], 'asset_type': 'document'}, files={'file': ('common.txt', b'COMMON_BRAND_FILE', 'text/plain')})).json()
    read = (await client.get(other_base + '/ai-sources/read', params={'key': 'file:' + asset['id']})).json()
    assert read['status'] == 'read' and read['content'] == 'COMMON_BRAND_FILE'


async def test_excluded_origin_removes_selected_answer_and_registered_tasks_keep_current_state(workspace, model, db):
    client, base, pid, cid = workspace
    message, _ = await candidates(workspace, model)
    proposal = message['proposals'][0]
    body = {'source_proposal_id': proposal['proposal_id'], 'title': '확인한 일', 'date': '2027-01-01'}
    response = await client.post(base + '/tasks/registrations', json={'request_id': 'one', 'tasks': [body]})
    task = response.json()['results'][0]['task']
    await db.tasks.update_one({'_id': ObjectId(task['id'])}, {'$set': {'done': True, 'deleted_at': utcnow()}})
    repeat = await client.post(base + '/tasks/registrations', json={'request_id': 'two', 'tasks': [{**body, 'title': '바꾸려는 내용'}]})
    current = repeat.json()['results'][0]
    assert current['status'] == 'already_linked' and current['task']['done'] and current['task']['status'] == 'trash'
    assert current['task']['title'] == '확인한 일'
    assert await db.tasks.count_documents({'project_id': ObjectId(pid)}) == 1
    await client.put(base + f'/conversations/{cid}/scope', json={'scope': {'kinds': []}, 'revision': 0})
    doc, url = await make_doc(workspace, origin_message_id=message['id'])
    reply = await client.post(url + '/sections/F01/assist', json={'action': 'draft'})
    assert reply.status_code == 422  # Mixed old answer cannot bypass inherited exclusions.
    assert all(s['text'] == '' for s in doc['sections'])


async def test_sixth_document_is_searchable_and_full_original_is_paginated(workspace):
    client, base, _, _ = workspace
    first, url = await make_doc(workspace)
    await client.put(url + '/sections/F01', json={'text': 'A' * 19000, 'revision': 0})
    await client.put(url + '/sections/F02', json={'text': 'B' * 3000 + 'OLD_DOC_LAST_FACT', 'revision': 0})
    for _ in range(5):
        await make_doc(workspace)
    search = (await client.get(base + '/ai-sources', params={'query': 'OLD_DOC_LAST_FACT'})).json()
    assert [s['key'] for s in search['items']] == ['doc:' + first['id']]
    original = (await client.get(base + '/ai-sources/read', params={'key': search['items'][0]['key']})).json()
    assert original['status'] == 'partial' and original['next_offset']
    rest = (await client.get(base + '/ai-sources/read', params={'key': original['key'], 'offset': original['next_offset']})).json()
    assert rest['status'] == 'read' and 'OLD_DOC_LAST_FACT' in rest['content']
    assert json.loads(original['content'] + rest['content'])[1]['text'].endswith('OLD_DOC_LAST_FACT')


async def test_model_cannot_complete_existing_task(workspace, model, db):
    client, base, pid, cid = workspace
    task = (await client.post(base + '/tasks', json={'title': '사용자가 관리할 일정', 'date': '2027-01-01'})).json()
    provider, runtime = model([[tool_call('complete_task', {'id': task['id']}), COMPLETED]])
    result = await client.post(base + '/runs', json={'text': '이 일 완료해 줘', 'conversation_id': cid})
    rid = result.json()['run_id']
    await asyncio.wait_for(runtime.tasks[rid], 10)
    assert (await client.get(base + '/runs/' + rid)).json()['status'] == 'failed'
    assert not (await db.tasks.find_one({'_id': ObjectId(task['id'])}))['done']
    assert 'complete_task' not in {t.get('name') for t in provider.calls[0]['tools']}
