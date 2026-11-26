// 메모·기록 옆 패널(프로토타입 work-context-panel). 메모는 입력을 멈추면 자동 저장하고, 최근 기록은 프로젝트의 recent를 보여 준다.
import { useEffect, useRef, useState } from 'react'
import { errorText, projects } from '../api/index.js'
import { formatDate } from './ui.jsx'
import { createMemoAutosave } from './memoAutosave.js'

const SAVE_DELAY_MS = 800

export default function MemoPanel({ projectId, open, onClose, refreshKey }) {
  const [project, setProject] = useState(null)
  const [note, setNote] = useState('')
  const [status, setStatus] = useState('자동 저장')
  const revision = useRef(0)
  const autosave = useRef(null)

  const load = (force = false) => projects.get(projectId).then((p) => {
    if (!force && autosave.current?.busy) return
    setProject(p); setNote(p.note || ''); revision.current = p.revision
  }).catch((err) => setStatus(errorText(err)))

  // 탭을 옮기면 최근 기록을 다시 읽는다(정산·문서 작업이 기록을 남긴다).
  useEffect(() => { if (open) load() }, [projectId, open, refreshKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (value) => {
    setStatus('저장 중…')
    try {
      const saved = await projects.update(projectId, { note: value, revision: revision.current })
      revision.current = saved.revision
      setProject(saved)
      setStatus('저장했어요')
      window.dispatchEvent(new CustomEvent('ax:project-updated', { detail: saved }))
    } catch (err) {
      // 다른 곳에서 먼저 바뀌었으면 최신 값을 다시 읽는다.
      setStatus(err.status === 409 ? '다른 곳에서 바뀌어 최신 메모를 불러왔어요' : errorText(err))
      if (err.status === 409) await load(true)
    }
  }
  useEffect(() => {
    const saver = createMemoAutosave(save, SAVE_DELAY_MS)
    autosave.current = saver
    return () => { void saver.flush().catch(() => {}) }
  }, [projectId]) // eslint-disable-line react-hooks/exhaustive-deps
  const change = (value) => {
    setNote(value); setStatus('입력 중…')
    autosave.current.schedule(value)
  }

  return (
    <aside className="work-context-panel" id="work-context-panel" aria-label="프로젝트 메모와 기록" hidden={!open}>
      <div className="row between">
        <h2>메모·기록</h2>
        <button type="button" aria-label="메모·기록 닫기" onClick={onClose}>×</button>
      </div>
      <label htmlFor="work-note">프로젝트 메모</label>
      <textarea id="work-note" placeholder="잊지 않고 싶은 생각을 적어두세요." value={note} onChange={(e) => change(e.target.value)} onBlur={() => { void autosave.current.flush().catch(() => {}) }} />
      <small role="status">{status}</small>
      <h3>최근 기록</h3>
      <div className="work-recent">
        {(project?.recent || []).slice(0, 12).map((item, index) => (
          <article key={index}><p>{item.text}</p><small>{formatDate(item.at)}</small></article>
        ))}
        {project && !project.recent?.length && <p className="muted small">아직 기록이 없어요.</p>}
      </div>
    </aside>
  )
}
