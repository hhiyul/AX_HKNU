// 항목별 AI 도움: 고른 참고만 보내 후보를 만들고, 적용하면 임시 입력에만 들어간다(기획서 v2 D절).
import { useEffect, useState } from 'react'
import { docs, errorText, plan } from '../api/index.js'
import { followRun } from '../api/stream.js'

export default function DocAssist({ projectId, doc, section, onApplied }) {
  const [open, setOpen] = useState(false)
  const [action, setAction] = useState('draft')
  const [request, setRequest] = useState('')
  const [planItems, setPlanItems] = useState([])
  const [picked, setPicked] = useState([])
  const [note, setNote] = useState('')
  const [streaming, setStreaming] = useState('')
  const [running, setRunning] = useState(false)
  const [candidate, setCandidate] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => { if (open) plan.list(projectId).then(setPlanItems).catch(() => setPlanItems([])) }, [open, projectId])
  useEffect(() => { setCandidate(null); setStreaming(''); setError(''); setAction(section.text ? 'refine' : 'draft') }, [section.section_id, section.text])

  const toggle = (ref) => setPicked((now) => (now.some((r) => r.type === ref.type && r.id === ref.id)
    ? now.filter((r) => !(r.type === ref.type && r.id === ref.id)) : [...now, ref]))
  const isPicked = (type, id) => picked.some((r) => r.type === type && r.id === id)

  const run = async () => {
    setError(''); setCandidate(null); setStreaming(''); setRunning(true)
    const references = [...picked, ...(note.trim() ? [{ type: 'note', text: note.trim() }] : [])]
    try {
      const { run_id: runId } = await docs.assist(projectId, doc.id, section.section_id, action, request, references)
      followRun(projectId, runId, {
        onEvent: (event) => { if (event.kind === 'delta') setStreaming((text) => text + event.content) },
        onDone: async (kind, content) => {
          setRunning(false)
          if (kind !== 'completed') { setError(content || '후보를 만들지 못했어요. 다시 시도해 주세요.'); return }
          const history = await docs.sectionMessages(projectId, doc.id, section.section_id)
          const last = [...history].reverse().find((m) => m.candidate)
          setCandidate(last || null)
        },
      })
    } catch (err) {
      setRunning(false)
      setError(err.code === 'RUN_ACTIVE' ? '이 프로젝트에서 AI가 다른 요청을 처리 중이에요. 끝난 뒤 다시 눌러 주세요.' : errorText(err))
    }
  }

  const apply = async (confirm = false) => {
    try {
      const updated = await docs.apply(projectId, doc.id, section.section_id, candidate.id, section.draft_revision, confirm)
      setCandidate(null); setStreaming('')
      onApplied(updated)
    } catch (err) {
      if (err.code === 'CANDIDATE_OUTDATED' && window.confirm('후보를 만든 뒤 글이 바뀌었어요. 그래도 후보로 바꿀까요? (지금 글은 임시 입력에서 사라져요)')) {
        apply(true)
      } else if (err.code !== 'CANDIDATE_OUTDATED') setError(errorText(err))
    }
  }

  const otherSections = doc.sections.filter((s) => s.section_id !== section.section_id && s.text)
  const filledPlan = planItems.filter((p) => p.content)

  return (
    <section className="card">
      <button type="button" className="assist-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span><strong>AI 도움</strong> <span className="muted small">· 고른 정보만 참고해서 후보를 만들어요</span></span>
        <span aria-hidden="true">{open ? '−' : '＋'}</span>
      </button>
      {open && (
        <div className="stack" style={{ gap: 16, marginTop: 18 }}>
          <div className="row wrap">
            <button type="button" className={action === 'draft' ? 'primary small' : 'small'} onClick={() => setAction('draft')}>초안 만들기</button>
            <button type="button" className={action === 'refine' ? 'primary small' : 'small'} disabled={!section.text && !section.draft} onClick={() => setAction('refine')}>현재 글 다듬기</button>
          </div>
          <div>
            <label htmlFor="assist-request">요청(선택)</label>
            <input id="assist-request" value={request} onChange={(e) => setRequest(e.target.value)} placeholder="예: 선물용 느낌으로" />
          </div>
          <div>
            <label>참고할 정보 고르기</label>
            <div className="reference-list">
              {filledPlan.map((p) => (
                <label key={p.key}>
                  <input type="checkbox" checked={isPicked('plan', p.key)} onChange={() => toggle({ type: 'plan', id: p.key })} />
                  <span>기본 기획 · {p.title}: <span className="muted">{p.content}</span></span>
                </label>
              ))}
              {otherSections.map((s) => (
                <label key={s.section_id}>
                  <input type="checkbox" checked={isPicked('section', s.section_id)} onChange={() => toggle({ type: 'section', id: s.section_id })} />
                  <span>이 문서 · {s.title}</span>
                </label>
              ))}
              {!filledPlan.length && !otherSections.length && <span className="muted small">고를 수 있는 기록이 아직 없어요.</span>}
            </div>
          </div>
          <div>
            <label htmlFor="assist-note">견적서·메모에서 옮겨 적기(선택)</label>
            <textarea id="assist-note" style={{ minHeight: 80 }} value={note} onChange={(e) => setNote(e.target.value)} placeholder="파일 내용은 자동으로 읽지 않아요. 필요한 부분만 붙여 넣어 주세요." />
          </div>
          <button type="button" className="blue" disabled={running} onClick={run}>{running ? '만드는 중…' : '후보 만들기'}</button>
          {error && <div className="notice error">{error}</div>}
          {(streaming || candidate) && (
            <div className="stack" style={{ gap: 10 }}>
              <span className="muted small">AI 후보 · 적용 전까지 본문은 그대로예요</span>
              <div className="candidate">{candidate?.candidate?.text || streaming}</div>
              {candidate && (
                <div className="editor-actions">
                  <button type="button" onClick={() => { setCandidate(null); setStreaming('') }}>닫기</button>
                  <button type="button" className="primary" onClick={() => apply(false)}>적용(임시 입력으로)</button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
