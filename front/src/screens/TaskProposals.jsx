// 할 일 제안: AI 후보는 아직 일정이 아니다. 날짜를 확인하고 등록한 것만 일정이 된다.
import { useCallback, useEffect, useState } from 'react'
import { docs, errorText, tasks } from '../api/index.js'
import { followRun } from '../api/stream.js'
import { go, paths } from '../router.js'

const today = () => new Date().toLocaleDateString('sv-SE')

export default function TaskProposals({ projectId, docId, section }) {
  const [proposals, setProposals] = useState([])
  const [dates, setDates] = useState({})
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    const history = await docs.sectionMessages(projectId, docId, section.section_id).catch(() => [])
    setProposals(history.flatMap((m) => m.proposals || []))
  }, [projectId, docId, section.section_id])
  useEffect(() => { load(); setError('') }, [load])

  const ask = async () => {
    setError(''); setRunning(true)
    try {
      const { run_id: runId } = await docs.proposeTasks(projectId, docId, section.section_id, '')
      followRun(projectId, runId, {
        onDone: (kind, content) => {
          setRunning(false)
          if (kind !== 'completed') setError(content || '할 일 후보를 받지 못했어요.')
          load()
        },
      })
    } catch (err) {
      setRunning(false)
      setError(err.code === 'RUN_ACTIVE' ? 'AI가 다른 요청을 처리 중이에요. 끝난 뒤 다시 눌러 주세요.' : errorText(err))
    }
  }

  const register = async (proposal) => {
    const date = dates[proposal.proposal_id] || proposal.suggested_date
    if (!date) { setError('날짜를 골라 주세요.'); return }
    try {
      await tasks.create(projectId, { title: proposal.title, date, note: proposal.note || '', source_proposal_id: proposal.proposal_id })
      setError('')
      load()
    } catch (err) {
      setError(errorText(err))
    }
  }

  return (
    <section className="card">
      <div className="row between wrap">
        <div>
          <h2>이 항목에서 할 일</h2>
          <p className="muted small">AI 후보는 아직 일정이 아니에요. 날짜를 고르고 등록한 것만 일정이 돼요.</p>
        </div>
        <button type="button" className="small" disabled={running} onClick={ask}>{running ? '찾는 중…' : '할 일 제안 받기'}</button>
      </div>
      {error && <div className="notice error" style={{ marginTop: 12 }}>{error}</div>}
      {!proposals.length && <p className="muted small" style={{ marginTop: 12 }}>견적 확인처럼 해야 할 일이 있으면 제안을 받아 일정에 등록해 보세요.</p>}
      <div className="stack" style={{ gap: 14, marginTop: 14 }}>
        {proposals.map((p) => (
          <div key={p.proposal_id} className="proposal">
            <div>
              <strong>{p.title}</strong>
              {p.note && <p className="muted small">{p.note}</p>}
            </div>
            {p.task_id ? (
              <div className="proposal-actions">
                <span className={`badge ${p.task_status === 'trash' ? '' : 'saved'}`}>{p.task_status === 'trash' ? '휴지통에 있음' : '등록됨'}</span>
                <button type="button" className="small" onClick={() => go(paths.tasks(projectId))}>일정 보기</button>
              </div>
            ) : (
              <div className="proposal-actions">
                <input type="date" min={today()} value={dates[p.proposal_id] || p.suggested_date || ''} aria-label={`${p.title} 날짜`}
                  onChange={(e) => setDates({ ...dates, [p.proposal_id]: e.target.value })} />
                <button type="button" className="primary small" onClick={() => register(p)}>일정 등록</button>
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}
