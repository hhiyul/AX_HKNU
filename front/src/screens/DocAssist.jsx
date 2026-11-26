import { useEffect, useRef, useState } from 'react'
import { aiSources, chat, conversations, docs, errorText, plan } from '../api/index.js'
import { followRun } from '../api/stream.js'
import Markdown from '../components/Markdown.jsx'
import { AnswerSources } from '../components/ReferenceScope.jsx'

export default function DocAssist({ projectId, doc, section, currentText, onApplied, beforeRequest }) {
  const [open, setOpen] = useState(false)
  const [action, setAction] = useState('draft')
  const [request, setRequest] = useState('')
  const [context, setContext] = useState(null)
  const [choices, setChoices] = useState([])
  const [threads, setThreads] = useState([])
  const [past, setPast] = useState([])
  const [pastId, setPastId] = useState('')
  const [pastMore, setPastMore] = useState(false)
  const [query, setQuery] = useState('')
  const [next, setNext] = useState(null)
  const [history, setHistory] = useState([])
  const [candidate, setCandidate] = useState(null)
  const [streaming, setStreaming] = useState('')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const stream = useRef(null)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false; stream.current?.() } }, [])
  useEffect(() => {
    if (!open) return
    let active = true
    Promise.all([docs.assistContext(projectId, doc.id, section.section_id), docs.sectionMessages(projectId, doc.id, section.section_id), plan.list(projectId), aiSources.search(projectId), conversations.list(projectId)])
      .then(([saved, messages, plans, page, list]) => { if (active) { setContext(saved); setHistory(messages); setChoices([...plans.filter((p) => p.content).map((p) => ({ key: p.key, kind: 'plan', title: p.title })), ...page.items]); setNext(page.next_offset); setThreads(list) } })
      .catch((e) => { if (active) setError(errorText(e)) })
    return () => { active = false }
  }, [open, projectId, doc.id, section.section_id])
  const reference = (source) => ({ type: source.kind === 'document' ? 'section' : source.kind, id: source.key.startsWith('ck_') ? source.key : source.key.split(':').slice(1).join(':') })
  const picked = (r) => context.references.some((x) => x.type === r.type && x.id === r.id)
  const toggle = (r) => setContext((old) => ({ ...old, references: picked(r) ? old.references.filter((x) => !(x.type === r.type && x.id === r.id)) : [...old.references, r] }))
  const search = async (offset = 0) => { try { const page = await aiSources.search(projectId, query, offset); setChoices((old) => offset ? [...old, ...page.items] : page.items); setNext(page.next_offset) } catch (e) { setError(errorText(e)) } }
  const loadPast = async (id, skip = 0) => {
    setPastId(id)
    if (!id) { setPast([]); setPastMore(false); return }
    try { const messages = await chat.messages(projectId, id, skip); setPast((old) => skip ? [...messages, ...old] : messages); setPastMore(messages.length === 100) } catch (e) { setError(errorText(e)) }
  }
  const saveSelection = async () => {
    const saved = await docs.saveAssistContext(projectId, doc.id, section.section_id, context)
    if (alive.current) setContext(saved)
    return saved
  }
  const run = async () => {
    setError(''); setCandidate(null); setStreaming(''); setRunning(true)
    try {
      await beforeRequest()
      await saveSelection()
      const { run_id: runId } = await docs.assist(projectId, doc.id, section.section_id, action, request)
      stream.current = followRun(projectId, runId, {
        onEvent: (event) => { if (alive.current && event.kind === 'delta') setStreaming((text) => text + event.content) },
        onDone: async (kind, content) => {
          if (!alive.current) return
          setRunning(false)
          if (kind !== 'completed') setError(content || '요청을 완료하지 못했습니다. 부분 답변은 이력에 남습니다.')
          try { const messages = await docs.sectionMessages(projectId, doc.id, section.section_id); if (alive.current) { setHistory(messages); setCandidate([...messages].reverse().find((m) => m.request_id === runId && m.candidate) || null); setStreaming('') } } catch (e) { if (alive.current) setError(errorText(e)) }
        },
      })
    } catch (e) { if (alive.current) { setRunning(false); setError(errorText(e)) } }
  }
  const apply = async (confirm = false, prepared = null) => {
    let saved = prepared
    try {
      saved ||= await beforeRequest()
      const updated = await docs.apply(projectId, doc.id, section.section_id, candidate.id, saved.draft_revision, confirm)
      setCandidate(null); onApplied(updated)
    } catch (e) {
      if (e.code === 'CANDIDATE_OUTDATED' && window.confirm('후보 작성 후 글이 바뀌었습니다. 비교한 후보로 임시 입력을 바꾸시겠습니까?')) apply(true, saved)
      else setError(errorText(e))
    }
  }
  return <section className="card stack">
    <button type="button" className="assist-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>AI 도움 · {section.title}</button>
    {open && <>
      <p>이 항목의 이전 대화와 같은 문서의 저장 내용을 참고합니다. 외부 자료는 아래에서 선택합니다.</p>
      {history.map((m) => <article key={m.id}><strong>{m.role === 'USER' ? '요청' : m.assist_action === 'check' ? 'AI 점검 의견' : 'AI 답변'}</strong><Markdown>{m.content}</Markdown>{m.role !== 'USER' && <AnswerSources projectId={projectId} docId={doc.id} sectionId={section.section_id} sources={m.source_manifest} />}{m.candidate && <button type="button" onClick={() => setCandidate(m)}>이 후보 비교</button>}</article>)}
      {context && <>
        <details><summary>선택한 외부 참고 {context.references.length}개</summary>
          {context.references.map((r, i) => <div className="agent-source" key={`${r.type}-${r.id}-${i}`}><span>{r.type} · {r.id || r.text}</span><button type="button" onClick={() => setContext((old) => ({ ...old, references: old.references.filter((_, n) => n !== i) }))}>해제</button></div>)}
          <div className="agent-actions"><label>자료 찾기<input value={query} onChange={(e) => setQuery(e.target.value)} /></label><button type="button" onClick={() => search()}>찾기</button></div>
          {choices.filter((s) => ['plan', 'file', 'product', 'contact', 'record'].includes(s.kind)).map((source) => { const ref = reference(source); return <label className="agent-source" key={source.key}><input type="checkbox" checked={picked(ref)} onChange={() => toggle(ref)} />{source.title}</label> })}
          {next != null && <button type="button" onClick={() => search(next)}>더 보기</button>}
          <details><summary>대화 답변으로 참고 교체·추가</summary>
            <label>참고할 대화<select value={pastId} onChange={(e) => loadPast(e.target.value)}><option value="">선택해 주세요</option>{threads.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}</select></label>
            {past.map((m) => <label className="agent-source" key={m.id}><input type="checkbox" checked={picked({ type: 'message', id: m.id })} onChange={() => toggle({ type: 'message', id: m.id })} /><span>{m.role === 'USER' ? '요청' : '답변'} · {m.content}</span></label>)}
            {pastMore && <button type="button" onClick={() => loadPast(pastId, past.length)}>이전 메시지 더 보기</button>}
          </details>
          {!!context.scope.excluded.length && <p>원 대화에서 제외한 자료 {context.scope.excluded.length}개는 계속 제외합니다.</p>}
          <button type="button" disabled={running} onClick={() => saveSelection().catch((e) => setError(errorText(e)))}>참고 선택 저장</button>
        </details>
        <label>작업<select value={action} disabled={running} onChange={(e) => setAction(e.target.value)}><option value="draft">초안 만들기</option><option value="refine">현재 글 다듬기</option><option value="check">AI 내용 점검(별도 요청)</option></select></label>
        <label>요청<textarea value={request} onChange={(e) => setRequest(e.target.value)} maxLength={2000} /></label>
        <button type="button" disabled={running} onClick={run}>{running ? '처리 중…' : action === 'check' ? 'AI 점검 요청' : '후보 요청'}</button>
      </>}
      {error && <p className="notice error" role="alert">{error}</p>}
      {streaming && <Markdown>{streaming}</Markdown>}
      {candidate && <div className="stack"><h3>후보 비교</h3><div className="agent-compare"><div><strong>현재 글</strong><pre className="agent-original">{currentText}</pre></div><div><strong>AI 후보</strong><pre className="agent-original">{candidate.candidate.text}</pre></div></div><button type="button" onClick={() => apply()}>이 항목의 임시 입력에 적용</button></div>}
    </>}
  </section>
}
