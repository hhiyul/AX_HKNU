// AI협업(프로토타입 채팅 틀). 대화를 여러 개 두고(?c=대화 id), 답변은 SSE로 받아 보여 준 뒤 목록을 다시 읽는다.
// 사진·파일은 먼저 프로젝트 자료로 올리고 메시지에 asset_ids로 붙인다. TXT·MD·DOCX는 서버 설정이 켜져 있을 때 AI가 읽는다.
import { useCallback, useEffect, useRef, useState } from 'react'
import { assets, chat, conversations, errorText, projects, runs } from '../api/index.js'
import { followRun } from '../api/stream.js'
import { FILE_ACCEPT, FileChips, PHOTO_ACCEPT, uploadFiles } from '../components/Attachments.jsx'
import ConversationPanel from '../components/ConversationPanel.jsx'
import Markdown from '../components/Markdown.jsx'
import { go, paths } from '../router.js'

const PURPOSE = { document_assist: '문서 작성 도움', task_proposal: '할 일 제안' }

const icon = (d) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
)

const wideScreen = () => window.matchMedia('(min-width: 1051px)').matches

export default function Chat({ projectId, conversationId }) {
  const [threads, setThreads] = useState(null)
  const [panelOpen, setPanelOpen] = useState(wideScreen)
  const [messages, setMessages] = useState([])
  // 지금 보이는 메시지가 어느 대화 것인지. 다 받아 오기 전에는 기본 안내 대신 "불러오는 중"을 보여 준다.
  const [loadedFor, setLoadedFor] = useState(undefined)
  const [files, setFiles] = useState({})
  const [pending, setPending] = useState([])
  const [uploading, setUploading] = useState(false)
  const [text, setText] = useState('')
  const [streaming, setStreaming] = useState('')
  // 보낸 질문을 서버 목록에 들어오기 전까지 바로 보여 준다(화면이 비었다가 다시 채워지지 않게).
  const [sentDraft, setSentDraft] = useState(null)
  const [running, setRunning] = useState(false)
  // 지금 따라가는 실행 id(중지 버튼용)와 중지 요청을 보냈는지.
  const [runId, setRunId] = useState(null)
  const [stopping, setStopping] = useState(false)
  const [elsewhere, setElsewhere] = useState(false)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  // 화면을 떠나면 열어 둔 스트림을 닫는다.
  const closeStream = useRef(null)
  useEffect(() => () => closeStream.current?.(), [])
  const bottom = useRef(null)
  const photoInput = useRef(null)
  const fileInput = useRef(null)
  const menu = useRef(null)

  // 주소에 대화가 없으면 가장 최근 대화를 연다.
  const currentId = conversationId || threads?.[0]?.id || null
  const current = threads?.find((t) => t.id === currentId)
  // 늦게 도착한 다른 대화의 응답이 지금 화면을 덮지 않게 마지막으로 연 대화를 기억한다.
  const shownId = useRef(currentId)
  shownId.current = currentId
  const ready = threads !== null && loadedFor === currentId
  const loadThreads = useCallback(() => conversations.list(projectId)
    .then((list) => { setThreads(list); return list })
    .catch((err) => { setError(errorText(err)); return null }), [projectId])
  const fetchMessages = useCallback((cid) => (cid ? chat.messages(projectId, cid) : Promise.resolve([])), [projectId])
  const showMessages = (list, cid) => {
    if (cid !== shownId.current) return
    setMessages(list)
    setLoadedFor(cid)
    // 보낸 질문이 목록에 들어왔으면 임시로 보여 주던 것을 지운다.
    setSentDraft((draft) => (draft && list.some((m) => m.role === 'USER' && m.content === draft.content) ? null : draft))
  }
  const load = useCallback(() => fetchMessages(currentId).then((list) => showMessages(list, currentId))
    .catch((err) => setError(errorText(err))), [fetchMessages, currentId]) // eslint-disable-line react-hooks/exhaustive-deps
  const select = (id) => { setError(''); setInfo(''); setStreaming(''); go(paths.chat(projectId), id ? { c: id } : undefined); if (!wideScreen()) setPanelOpen(false) }
  const startNew = async () => {
    if (running) return
    try {
      const created = await conversations.create(projectId)
      await loadThreads()
      select(created.id)
    } catch (err) {
      setError(errorText(err))
    }
  }
  const afterChange = (trashedId) => { loadThreads().then(() => { if (trashedId && trashedId === currentId) select(null) }) }
  const loadFiles = useCallback(() => assets.list(projectId)
    .then((list) => setFiles(Object.fromEntries(list.map((a) => [a.id, a])))).catch(() => setFiles({})), [projectId])
  useEffect(() => { loadThreads(); loadFiles() }, [loadThreads, loadFiles])
  useEffect(() => { if (threads) load() }, [load, threads])
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'end' }) }, [messages, streaming])

  const attach = async (event, kind) => {
    const chosen = [...event.target.files]
    event.target.value = ''
    menu.current?.removeAttribute('open')
    if (!chosen.length) return
    setUploading(true); setError('')
    const results = await uploadFiles(projectId, chosen, kind)
    setUploading(false)
    const added = results.filter((r) => r.asset).map((r) => r.asset)
    setPending((now) => [...now, ...added])
    setFiles((now) => ({ ...now, ...Object.fromEntries(added.map((a) => [a.id, a])) }))
    const failed = results.filter((r) => r.error).map((r) => r.error)
    if (failed.length) setError(failed.join('\n'))
  }

  const send = async (event) => {
    event?.preventDefault()
    if (!text.trim() || running || uploading) return
    const question = text.trim()
    const attachedNow = pending
    setError(''); setInfo(''); setRunning(true); setStreaming('')
    setSentDraft({ content: question, files: attachedNow })
    setText(''); setPending([])
    try {
      const { run_id: runId } = await chat.send(projectId, question, 'chat', attachedNow.map((a) => a.id), currentId)
      // 이 실행이 속한 대화. 대화가 없던 첫 질문이면 서버가 만든 대화(가장 최근)를 받아 주소에 고정한다.
      let cid = currentId
      if (!cid) {
        cid = (await loadThreads())?.[0]?.id || null
        if (cid) go(paths.chat(projectId), { c: cid })
      }
      follow(runId, cid)
    } catch (err) {
      // 보내지 못했으면 쓰던 글과 첨부를 입력칸에 되돌린다.
      setRunning(false); setSentDraft(null)
      setText(question); setPending(attachedNow)
      if (err.code !== 'RUN_ACTIVE') { setError(errorText(err)); return }
      // 이전 답변이 아직 만들어지는 중이면(새로고침 직후 등) 그 답변을 이어서 보여 준다.
      const resumed = await resume(messages)
      setError(resumed ? '' : 'AI가 다른 요청을 처리 중이에요. 잠시 뒤 다시 보내 주세요.')
      if (resumed) setInfo('이전 답변을 아직 만들고 있어요. 끝나면 다시 보내 주세요.')
    }
  }

  // 실행 하나를 끝까지 따라간다. 새로 보낸 질문과, 새로고침·화면 이동 뒤 이어 받는 답변이 함께 쓴다.
  // elsewhere: 다른 대화의 실행이라 이 화면에는 답변 글을 그리지 않고 안내만 한다.
  const follow = (runId, cid, { elsewhere = false } = {}) => {
    closeStream.current?.()
    setRunId(runId); setRunning(true); setStreaming(''); setElsewhere(elsewhere)
    closeStream.current = followRun(projectId, runId, {
      onEvent: (e) => {
        if (e.kind === 'delta') setStreaming((now) => now + e.content)
        if (e.kind === 'document_route') setStreaming('')
      },
      onDone: async (kind, content) => {
        // 저장된 답변을 먼저 받아 오고, 받은 목록과 흘러나오던 글을 한 번에 바꾼다(사라지거나 두 번 보이지 않게).
        const list = await fetchMessages(cid).catch(() => null)
        if (list) showMessages(list, cid)
        setInfo(kind === 'stopped' ? '답변을 멈췄어요. 이어서 다른 질문을 보내도 돼요.' : '')
        if (kind !== 'completed' && kind !== 'stopped') setError(content || '답변을 받지 못했어요.')
        setStreaming(''); setRunning(false); setSentDraft(null); setRunId(null); setStopping(false); setElsewhere(false)
        closeStream.current = null
        loadThreads()
      },
    })
  }

  // 이 프로젝트에서 아직 답변을 만드는 실행이 있으면 이어 받는다. 이어 받았으면 true.
  const resume = async (shown) => {
    const project = await projects.get(projectId).catch(() => null)
    const activeId = project?.active_run
    if (!activeId) return false
    const run = await runs.get(projectId, activeId).catch(() => null)
    if (run?.status !== 'running') return false
    const here = shown.some((m) => m.id === run.user_message_id)
    follow(activeId, currentId, { elsewhere: !here })
    if (!here) setInfo('다른 대화에서 답변을 만들고 있어요. 끝나면 이 대화에서도 보낼 수 있어요.')
    return true
  }
  // 대화를 처음 다 불러왔을 때 한 번 확인한다(새로고침·다른 탭에 갔다 온 경우).
  const resumeChecked = useRef(false)
  useEffect(() => {
    if (!ready || resumeChecked.current || running) return
    resumeChecked.current = true
    resume(messages)
  }, [ready]) // eslint-disable-line react-hooks/exhaustive-deps
  // 중지는 요청만 보낸다. 실제 마무리(목록 다시 읽기 등)는 스트림의 stopped 사건에서 한다.
  const stop = async () => {
    if (!runId || stopping) return
    setStopping(true)
    try {
      await runs.stop(projectId, runId)
    } catch (err) {
      setStopping(false)
      setError(errorText(err))
    }
  }
  // 폰 키보드의 줄바꿈과 겹치지 않게, PC에서만 Enter로 보낸다(Shift+Enter는 줄바꿈).
  const onKey = (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && window.matchMedia('(hover: hover)').matches) send(event)
  }
  const attached = (m) => (m.asset_ids || []).map((id) => files[id] || { id, file_name: '첨부 파일' })

  return (
    <div className={`chat-layout${panelOpen ? ' conversation-panel-open' : ''}`}>
      <section className="chat">
        <div className="chat-local-toolbar">
          <span className="chat-conversation-label">{threads === null ? '' : current?.title || (currentId ? '새 대화' : '첫 질문을 보내면 대화가 시작돼요')}</span>
          <div className="chat-mobile-actions">
            <button type="button" disabled={running} onClick={startNew}>＋ 새 대화</button>
            <button type="button" className="chat-history-toggle" aria-expanded={panelOpen} onClick={() => setPanelOpen(!panelOpen)}>대화 목록 ☷</button>
          </div>
        </div>
        <div className="messages">
          {!ready && !sentDraft && !running && <p className="muted small" role="status">대화를 불러오는 중…</p>}
          {ready && !messages.length && !streaming && !sentDraft && !running && (
            <article className="msg assistant">
              <div className="name">AX STUDIO</div>
              <p>기본 기획과 지금까지 쓴 문서를 바탕으로 답해요. 어떤 점이 궁금한가요?</p>
            </article>
          )}
          {(ready ? messages : []).map((m) => (m.role === 'USER' ? (
            <div key={m.id} className="msg user"><FileChips items={attached(m)} /><p>{m.content}</p></div>
          ) : (
            <article key={m.id} className="msg assistant">
              <div className="name">AX STUDIO{m.purpose && <span className="muted"> · {PURPOSE[m.purpose] || m.purpose}</span>}</div>
              <Markdown>{m.content}</Markdown>
            </article>
          )))}
          {sentDraft && <div className="msg user"><FileChips items={sentDraft.files} /><p>{sentDraft.content}</p></div>}
          {(streaming || running) && !elsewhere && !(!sentDraft && messages[messages.length - 1]?.role === 'ASSISTANT') && (
            <article className="msg assistant">
              <div className="name">AX STUDIO <span className="muted">· 답변 중</span></div>
              {streaming ? <Markdown>{streaming}</Markdown> : <p className="muted">생각하고 있어요…</p>}
            </article>
          )}
          {info && <div className="notice" role="status">{info}</div>}
          {error && <div className="notice error" style={{ whiteSpace: 'pre-line' }}>{error}</div>}
          <div ref={bottom} />
        </div>
        <form className="composer" onSubmit={send}>
          <FileChips items={pending} onRemove={(id) => setPending((now) => now.filter((a) => a.id !== id))} />
          <details className="chat-add-menu" ref={menu}>
            <summary className="chat-icon" aria-label="사진이나 파일 첨부">{icon('M12 5v14M5 12h14')}</summary>
            <div>
              <button type="button" onClick={() => photoInput.current?.click()}>사진 첨부</button>
              <button type="button" onClick={() => fileInput.current?.click()}>파일 첨부</button>
              <small>파일당 2MB · 사진(PNG·JPG·WebP), 문서(TXT·MD·DOCX)</small>
            </div>
          </details>
          <input ref={photoInput} type="file" hidden accept={PHOTO_ACCEPT} multiple onChange={(e) => attach(e, 'photo')} />
          <input ref={fileInput} type="file" hidden accept={FILE_ACCEPT} multiple onChange={(e) => attach(e, 'file')} />
          <label htmlFor="message" className="sr-only">메시지</label>
          <textarea id="message" rows={1} placeholder={uploading ? '파일을 올리는 중…' : '어떤 점이 궁금한가요?'} value={text} onKeyDown={onKey}
            onChange={(e) => setText(e.target.value)} />
          {running ? (
            <button className="send" type="button" aria-label={stopping ? '멈추는 중' : '답변 멈추기'} title="답변 멈추기"
              disabled={!runId || stopping} onClick={stop}>
              <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><rect width="12" height="12" rx="2" fill="currentColor" /></svg>
            </button>
          ) : (
            <button className="send" type="submit" aria-label="메시지 전송" disabled={uploading || !text.trim()}>{icon('M12 19V5m-6 6 6-6 6 6')}</button>
          )}
        </form>
      </section>
      <button type="button" className="conversation-backdrop" aria-label="대화 목록 닫기" onClick={() => setPanelOpen(false)} />
      <div className="conversation-history-region">
        <ConversationPanel projectId={projectId} list={threads || []} currentId={currentId} open={panelOpen}
          onSelect={select} onNew={startNew} onClose={() => setPanelOpen(false)} onChanged={afterChange} />
      </div>
    </div>
  )
}
