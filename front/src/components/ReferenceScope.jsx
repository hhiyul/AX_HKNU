import { useEffect, useState } from 'react'
import { aiSources, assets, chat, conversations, download, errorText } from '../api/index.js'
import AgentDialog from './AgentDialog.jsx'

const LABELS = { project: '프로젝트', brand: '브랜드 공통', plan: '기획 항목', record: '기록', document: '문서', task: '할 일', economics: '원가', ledger: '정산', memo: '메모', file: '파일', product: '제품', contact: '거래처' }

export default function ReferenceScope({ projectId, conversationId, onClose }) {
  const [state, setState] = useState(null)
  const [items, setItems] = useState([])
  const [query, setQuery] = useState('')
  const [next, setNext] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [threads, setThreads] = useState([])
  const [past, setPast] = useState([])
  useEffect(() => {
    let active = true
    Promise.all([conversations.scope(projectId, conversationId), aiSources.search(projectId)]).then(([saved, page]) => {
      if (active) { setState(saved); setItems(page.items); setNext(page.next_offset) }
    }).catch((e) => { if (active) setError(errorText(e)) })
    return () => { active = false }
  }, [projectId, conversationId])
  useEffect(() => { conversations.list(projectId).then(setThreads).catch((e) => setError(errorText(e))) }, [projectId])
  const toggle = (field, value) => setState((old) => ({ ...old, scope: { ...old.scope,
    [field]: old.scope[field].includes(value) ? old.scope[field].filter((v) => v !== value) : [...old.scope[field], value],
  } }))
  const search = async (offset = 0) => {
    setBusy(true)
    try { const page = await aiSources.search(projectId, query, offset); setItems((old) => offset ? [...old, ...page.items] : page.items); setNext(page.next_offset); setError('') }
    catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  const save = async () => {
    setBusy(true)
    try { await conversations.saveScope(projectId, conversationId, state.scope, state.revision); onClose() }
    catch (e) { setError(errorText(e)); setBusy(false) }
  }
  return <AgentDialog title="참고 범위" onClose={onClose}>
    <p>변경한 범위는 다음 요청부터 적용합니다. 이미 작성한 답변은 유지합니다.</p>
    {error && <p className="notice error" role="alert">{error}</p>}
    {!state ? <p role="status">불러오는 중…</p> : <>
      <fieldset><legend>포함할 정보</legend><div className="agent-options">{Object.entries(LABELS).map(([key, label]) =>
        <label key={key}><input type="checkbox" checked={state.scope.kinds.includes(key)} onChange={() => toggle('kinds', key)} />{label}</label>)}</div></fieldset>
      {!state.scope.kinds.length && <p>외부 참고 없이 현재 대화로 이어갑니다.</p>}
      <details><summary>직접 고른 이전 대화 {state.scope.message_ids.length}개</summary>
        <label>대화 선택<select defaultValue="" onChange={(e) => { setPast([]); if (e.target.value) chat.messages(projectId, e.target.value).then(setPast).catch((err) => setError(errorText(err))) }}><option value="">선택해 주세요</option>{threads.filter((t) => t.id !== conversationId).map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}</select></label>
        {past.map((m) => <label className="agent-source" key={m.id}><input type="checkbox" checked={state.scope.message_ids.includes(m.id)} onChange={() => toggle('message_ids', m.id)} /><span>{m.role === 'USER' ? '요청' : '답변'} · {m.content}</span></label>)}
        {state.scope.message_ids.filter((id) => !past.some((m) => m.id === id)).map((id) => <div key={id}>선택한 메시지 {id}<button type="button" onClick={() => toggle('message_ids', id)}>해제</button></div>)}
      </details>
      <form className="agent-actions" onSubmit={(event) => { event.preventDefault(); search() }}>
        <label>자료 찾기<input value={query} onChange={(e) => setQuery(e.target.value)} /></label><button disabled={busy}>찾기</button>
      </form>
      {items.map((item) => <label className="agent-source" key={item.key}>
        <input type="checkbox" checked={!state.scope.excluded.includes(item.key)} onChange={() => toggle('excluded', item.key)} />
        <span>{item.title} <small>· {LABELS[item.kind]}</small></span>
      </label>)}
      {next !== null && <button type="button" disabled={busy} onClick={() => search(next)}>자료 더 보기</button>}
      {!!state.scope.excluded.length && <details><summary>제외한 자료 {state.scope.excluded.length}개</summary>{state.scope.excluded.map((key) =>
        <div className="agent-source" key={key}>{items.find((item) => item.key === key)?.title || key}<button type="button" onClick={() => toggle('excluded', key)}>제외 해제</button></div>)}</details>}
      <div className="agent-actions"><button type="button" disabled={busy} onClick={save}>적용</button><button type="button" onClick={onClose}>취소</button></div>
    </>}
  </AgentDialog>
}

export function AnswerSources({ projectId, conversationId, docId, sectionId, sources }) {
  const [opened, setOpened] = useState(false)
  const [original, setOriginal] = useState(null)
  const [error, setError] = useState('')
  const read = async (key, offset = 0) => {
    try { const result = await aiSources.read(projectId, key, offset, conversationId, docId, sectionId); setOriginal(result); setError('') }
    catch (e) { setError(errorText(e)) }
  }
  const unique = [...new Map((sources || []).map((s) => [s.key, s])).values()]
  if (!unique.length) return null
  return <><button type="button" onClick={() => setOpened(true)}>참고 자료 {unique.length}개</button>{opened &&
    <AgentDialog title="답변 참고 자료" onClose={() => { setOpened(false); setOriginal(null) }}>
      <p>답변 작성 당시 참고한 자료입니다. 원본은 이후 변경되었을 수 있습니다.</p>
      {unique.map((s) => <div className="agent-source" key={s.key}><span>{s.title} · {({ partial: '일부 읽음', metadata: '목록 확인', read: '읽음', unsupported: '미지원', not_read: '아직 읽지 않음', failed: '읽기 실패', unavailable: '접근 불가', disabled: '읽기 꺼짐' })[s.status] || '상태 미확인'}</span><button type="button" onClick={() => read(s.key)}>원본 확인</button></div>)}
      {error && <p role="alert">{error}</p>}
      {original && <><p>{({ unavailable: '현재 범위에서 원본을 확인할 수 없습니다.', unsupported: '텍스트 원문 읽기를 지원하지 않는 파일입니다.', disabled: '파일 원문 읽기가 꺼져 있습니다.', failed: '파일 원문을 읽지 못했습니다.' })[original.status]}</p><pre className="agent-original">{original.content}</pre>{original.key?.startsWith('file:') && <button type="button" onClick={() => download(assets.fileUrl(original.key.slice(5)), original.title).catch((e) => setError(errorText(e)))}>원본 파일 다운로드</button>}{original.next_offset != null && <button type="button" onClick={() => read(original.key, original.next_offset)}>다음 부분</button>}</>}
    </AgentDialog>}</>
}
