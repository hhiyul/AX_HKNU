// 항목형 문서 편집(프로토타입 문서 편집 틀). 목차 → 현재 항목: 질문·가이드, 본문(임시 저장·저장·검토 완료), AI 도움, 할 일 제안.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { docs, download, errorText } from '../api/index.js'
import { go, paths, setLeaveGuard } from '../router.js'
import { Progress, StateBadge, TEMPLATE_NAME } from '../components/ui.jsx'
import DocAssist from './DocAssist.jsx'
import TaskProposals from './TaskProposals.jsx'

export default function DocEditor({ projectId, docId, sectionId }) {
  const [doc, setDoc] = useState(null)
  const [value, setValue] = useState('')
  const [notice, setNotice] = useState(null)
  const [preview, setPreview] = useState(null)

  const load = useCallback(() => docs.get(projectId, docId).then(setDoc).catch((err) => setNotice({ error: errorText(err) })),
    [projectId, docId])
  useEffect(() => { load() }, [load])

  const current = doc?.sections.find((s) => s.section_id === (sectionId || doc.last_section_id)) || doc?.sections[0]
  const base = current ? (current.draft ?? current.text) : ''
  useEffect(() => { setValue(base) }, [current?.section_id, base])
  const dirty = Boolean(current) && value !== base
  const replace = (section) => setDoc((now) => (now
    ? { ...now, sections: now.sections.map((s) => (s.section_id === section.section_id ? section : s)) } : now))

  // 다른 항목·다른 화면으로 갈 때 쓰던 글을 임시 저장한다. 저장에 실패하면 머물지 버릴지 묻는다.
  // 가드는 한 번만 걸고, 그때그때의 글은 ref로 읽는다.
  const latest = useRef(null)
  useLayoutEffect(() => { latest.current = { dirty, value, current } })
  useEffect(() => setLeaveGuard(async () => {
    const { dirty: unsaved, value: text, current: section } = latest.current
    if (!unsaved) return true
    try {
      replace(await docs.saveDraft(projectId, docId, section.section_id, text, section.draft_revision))
      setNotice({ ok: `「${section.title}」에 쓰던 글을 임시 저장했어요.` })
      return true
    } catch {
      return window.confirm('쓰던 글을 임시 저장하지 못했어요. 저장하지 않고 이동할까요?\n(취소를 누르면 이 화면에 남아요. 이동하면 쓰던 글은 사라져요.)')
    }
  }), [projectId, docId]) // eslint-disable-line react-hooks/exhaustive-deps
  // 새로고침·창 닫기는 저장할 틈이 없으니 브라우저 확인 창을 띄운다.
  useEffect(() => {
    if (!dirty) return undefined
    const warn = (event) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  if (!doc || !current) {
    return <div className="workspace-page">{notice?.error ? <div className="notice error">{notice.error}</div> : <p className="muted">불러오는 중…</p>}</div>
  }
  const act = async (work, message) => {
    try {
      const section = await work()
      replace(section)
      setNotice(message ? { ok: message } : null)
      load()
    } catch (err) {
      setNotice({ error: err.status === 409 ? '다른 곳에서 먼저 바뀌었어요. 최신 내용을 불러왔어요.' : errorText(err) })
      load()
    }
  }
  // 쓰던 글은 이동할 때 가드가 임시 저장한다.
  const select = (id) => {
    if (id === current.section_id) return
    if (!dirty) setNotice(null)
    go(paths.doc(projectId, docId), { s: id })
  }
  const openPreview = async () => {
    try { setPreview(await docs.preview(projectId, docId)) } catch (err) { setNotice({ error: errorText(err) }) }
  }
  const save = (fmt) => download(docs.exportUrl(projectId, docId, fmt), `${doc.title}.${fmt}`).catch(() => setNotice({ error: '내보내기를 하지 못했어요.' }))

  return (
    <div className="workspace-page">
      <div className="doc-editor">
        <header className="page-head row between wrap">
          <div>
            <div className="eyebrow">{TEMPLATE_NAME[doc.template_id] || 'DOCUMENT'}</div>
            <h1>{doc.title}</h1>
            <Progress value={doc.progress.required_reviewed} total={doc.progress.required_total} />
          </div>
          <div className="row wrap">
            <button type="button" className="small" onClick={() => go(paths.docs(projectId))}>← 저장함</button>
            <button type="button" className="small" onClick={openPreview}>미리보기</button>
            <button type="button" className="small" onClick={() => save('pdf')}>PDF</button>
            <button type="button" className="small" onClick={() => save('docx')}>DOCX</button>
          </div>
        </header>

        <nav className="section-tabs" aria-label="문서 항목">
          {doc.sections.map((s) => (
            <button key={s.section_id} type="button" aria-current={s.section_id === current.section_id ? 'true' : undefined} onClick={() => select(s.section_id)}>
              <span className={`state-dot ${s.state}`} aria-hidden="true" />{s.title}{s.required ? '' : ' (선택)'}
            </button>
          ))}
        </nav>

        <section className="editor-paper">
          <div className="row between">
            <label className="eyebrow" htmlFor="section-body">{current.title}</label>
            <StateBadge state={current.state} />
          </div>
          <p className="section-question">{current.question}</p>
          <p className="muted small">{current.guide}</p>
          <div className="divider" />
          {current.has_draft && !dirty && <div className="notice warn" style={{ marginBottom: 12 }}>임시 작성 중인 글이에요. 저장해야 문서에 반영돼요.</div>}
          <textarea id="section-body" className="editor-body" value={value} onChange={(e) => setValue(e.target.value)}
            placeholder="여기에 직접 써 주세요. 모르는 내용은 '확인 필요'로 남겨도 돼요." />
          {notice?.error && <div className="notice error">{notice.error}</div>}
          {notice?.ok && <div className="notice">{notice.ok}</div>}
          <footer className="editor-foot">
            <span>{value.length}자</span>
            <div className="editor-actions">
              <button type="button" disabled={!dirty} onClick={() => act(() => docs.saveDraft(projectId, docId, current.section_id, value, current.draft_revision), '임시 저장했어요.')}>임시 저장</button>
              <button type="button" className="primary" disabled={!value.trim() || (!dirty && !current.has_draft)}
                onClick={() => act(() => docs.save(projectId, docId, current.section_id, value, current.revision), '저장했어요. 내용을 확인했으면 검토 완료를 눌러 주세요.')}>저장하기</button>
              <button type="button" className="blue" disabled={dirty || current.has_draft || current.state !== 'WRITING'}
                onClick={() => act(() => docs.review(projectId, docId, current.section_id, current.revision), '검토 완료로 표시했어요.')}>검토 완료</button>
            </div>
          </footer>
        </section>

        <div className="gap" />
        <DocAssist projectId={projectId} doc={doc} section={current}
          onApplied={(section) => { replace(section); setNotice({ ok: 'AI 후보를 임시 입력에 넣었어요. 확인 후 저장해 주세요.' }); load() }} />
        <div className="gap" />
        <TaskProposals projectId={projectId} docId={docId} section={current} />

        {preview && (
          <>
            <div className="gap" />
            <section className="card">
              <div className="row between"><h2>미리보기</h2><button type="button" className="small" onClick={() => setPreview(null)}>닫기</button></div>
              {preview.has_unsaved_drafts && <div className="notice warn" style={{ marginTop: 12 }}>저장하지 않은 임시 글은 미리보기와 내보내기에 들어가지 않아요.</div>}
              <p style={{ marginTop: 12 }}>{preview.text}</p>
            </section>
          </>
        )}
      </div>
    </div>
  )
}
