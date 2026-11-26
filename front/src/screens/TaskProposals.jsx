// 문서와 일반 대화가 같은 확인·등록 컴포넌트를 사용한다.
import { useCallback, useEffect, useRef, useState } from 'react'
import { docs, errorText } from '../api/index.js'
import { followRun } from '../api/stream.js'
import TaskConfirmation from '../components/TaskConfirmation.jsx'

export default function TaskProposals({ projectId, docId, section, purposeIds = [] }) {
  const [proposals, setProposals] = useState([])
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const close = useRef(null)
  const sequence = useRef(0)
  const load = useCallback(async () => {
    const request = ++sequence.current
    try {
      const history = await docs.sectionMessages(projectId, docId, section.section_id)
      if (request === sequence.current) setProposals(history.flatMap((m) => m.proposals || []))
    } catch (e) { if (request === sequence.current) setError(errorText(e)) }
  }, [projectId, docId, section.section_id])
  useEffect(() => {
    let active = true
    docs.sectionMessages(projectId, docId, section.section_id).then((history) => { if (active) setProposals(history.flatMap((m) => m.proposals || [])) }).catch((e) => { if (active) setError(errorText(e)) })
    return () => { active = false }
  }, [projectId, docId, section.section_id])
  useEffect(() => () => close.current?.(), [])
  const ask = async () => {
    setError(''); setRunning(true)
    try {
      const { run_id: runId } = await docs.proposeTasks(projectId, docId, section.section_id, '')
      close.current = followRun(projectId, runId, {
        onDone: (kind, content) => {
          setRunning(false)
          if (kind !== 'completed') setError(content || '할 일 후보를 받지 못했어요.')
          load()
        },
      })
    } catch (e) { setRunning(false); setError(errorText(e)) }
  }
  return <section className="card">
    <h2>이 항목에서 할 일</h2>
    <p>필요한 일과 날짜를 확인한 뒤 등록합니다.</p>
    <div className="agent-actions">
      <button type="button" disabled={running} onClick={ask}>{running ? '찾는 중…' : '할 일 제안 받기'}</button>
      <TaskConfirmation projectId={projectId} proposals={proposals} originKey={`${docId}:${section.section_id}`} onChanged={load} allowedPurposes={purposeIds} />
    </div>
    {error && <p className="notice error" role="alert">{error}</p>}
  </section>
}
