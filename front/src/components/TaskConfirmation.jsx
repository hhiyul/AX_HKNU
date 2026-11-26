import { useEffect, useRef, useState } from 'react'
import { errorText, tasks } from '../api/index.js'
import { go, paths } from '../router.js'
import AgentDialog from './AgentDialog.jsx'
import { mergeTaskResults, isLinkedTask } from './registrationState.js'

const PURPOSES = { funding: '펀딩', business_plan: '사업계획서' }

export default function TaskConfirmation({ projectId, proposals, originKey, onChanged, allowedPurposes = ['funding', 'business_plan'] }) {
  const [open, setOpen] = useState(false)
  if (!proposals?.length) return null
  return <><button type="button" onClick={() => setOpen(true)}>할 일 {proposals.length}개 확인·등록</button>
    {open && <Confirmation projectId={projectId} proposals={proposals} originKey={originKey} onChanged={onChanged} allowedPurposes={allowedPurposes} onClose={() => setOpen(false)} />}</>
}

function Confirmation({ projectId, proposals, originKey, onChanged, allowedPurposes, onClose }) {
  const storageKey = `ax-task-registration:${projectId}:${originKey}`
  const [rows, setRows] = useState(() => proposals.map((p) => ({ ...p, selected: false, date: p.suggested_date || '', purpose_ids: [] })))
  const [operation, setOperation] = useState(() => sessionStorage.getItem(storageKey))
  const [results, setResults] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const sending = useRef(false)
  const unknown = operation && (!results.length || results.some((r) => r.status === 'unknown'))
  const recover = async (id = operation) => {
    if (!id) return
    setBusy(true)
    try { const saved = await tasks.registration(projectId, id); setResults((old) => mergeTaskResults(old, saved.results)); setError(''); await onChanged?.() }
    catch (e) { setError(e.status === 404 ? '접수 여부가 아직 확인되지 않았습니다. 원 요청 결과를 다시 확인해 주세요.' : errorText(e)) }
    finally { setBusy(false) }
  }
  useEffect(() => {
    if (!operation) return
    let active = true
    tasks.registration(projectId, operation).then((saved) => { if (active) setResults((old) => mergeTaskResults(old, saved.results)) }).catch((e) => { if (active) setError(errorText(e)) })
    return () => { active = false }
  }, [projectId, operation])
  const update = (id, patch) => setRows((old) => old.map((r) => r.proposal_id === id ? { ...r, ...patch } : r))
  const existing = (row) => results.find((r) => r.proposal_id === row.proposal_id)
  const linked = (row) => isLinkedTask(row, existing(row))
  const selected = rows.filter((r) => r.selected && !linked(r))
  const register = async (event) => {
    event.preventDefault()
    if (sending.current || unknown || !selected.length) return
    const requestId = crypto.randomUUID()
    const payload = selected.map((r) => ({ source_proposal_id: r.proposal_id, title: r.title.trim(), date: r.date, note: r.note || '', purpose_ids: r.purpose_ids }))
    sending.current = true; setBusy(true); setError(''); setResults((old) => mergeTaskResults(old, selected.map((r) => ({ proposal_id: r.proposal_id, status: 'unknown' })))); setOperation(requestId)
    sessionStorage.setItem(storageKey, requestId)
    try { const saved = await tasks.register(projectId, requestId, payload); setResults((old) => mergeTaskResults(old, saved.results)); await onChanged?.() }
    catch (e) {
      // 422/409는 쓰기 전에 거부된 요청. 연결 오류/5xx는 결과 조회 전까지 재전송하지 않는다.
      if (e.status === 422 || e.status === 409) { setOperation(null); sessionStorage.removeItem(storageKey) }
      setError(errorText(e))
    } finally { sending.current = false; setBusy(false) }
  }
  return <AgentDialog title="할 일과 날짜 확인" onClose={onClose}>
    <p>선택한 일만 등록합니다. 등록해도 문서 완료나 할 일 실행 완료로 바뀌지 않습니다.</p>
    <form onSubmit={register}>
      {rows.map((row) => {
        const result = existing(row)
        const task = result?.task || row.task
        return <fieldset className="agent-task" key={row.proposal_id} disabled={busy || !!linked(row) || !!unknown}>
          <legend><label><input type="checkbox" checked={row.selected && !linked(row)} onChange={(e) => update(row.proposal_id, { selected: e.target.checked })} />{row.title}</label></legend>
          {linked(row) ? <p>{task?.title || row.title} · {task?.date || row.suggested_date} · {result?.task?.status === 'trash' || row.task_status === 'trash' ? '휴지통에 있는 연결 일정' : task?.done ? '실행 완료' : '등록됨 · 미완료'}</p> : <>
            <label>할 일 내용<input required={row.selected} maxLength={200} value={row.title} onChange={(e) => update(row.proposal_id, { title: e.target.value })} /></label>
            <label>날짜<input type="date" required={row.selected} value={row.date} onChange={(e) => update(row.proposal_id, { date: e.target.value })} /></label>
            <label>메모<textarea maxLength={1000} value={row.note || ''} onChange={(e) => update(row.proposal_id, { note: e.target.value })} /></label>
            <div className="agent-options">{allowedPurposes.map((p) => <label key={p}><input type="checkbox" checked={row.purpose_ids.includes(p)} onChange={() => update(row.proposal_id, { purpose_ids: row.purpose_ids.includes(p) ? row.purpose_ids.filter((x) => x !== p) : [...row.purpose_ids, p] })} />{PURPOSES[p]}</label>)}</div><small>목적은 선택하지 않아도 등록할 수 있습니다.</small>
          </>}
          {result?.status === 'failed' && <p className="notice error">{result.error.message}</p>}
        </fieldset>
      })}
      {error && <p className="notice error" role="alert">{error}</p>}
      {unknown && <p role="status">등록 결과를 확인 중입니다. 같은 요청을 다시 등록하지 않습니다.</p>}
      <div className="agent-actions">
        <button type="submit" disabled={busy || !!unknown || !selected.length}>선택한 {selected.length}개 등록</button>
        {operation && <button type="button" disabled={busy} onClick={() => recover()}>등록 결과 다시 확인</button>}
        <button type="button" onClick={() => go(paths.tasks(projectId))}>일정 보기</button><button type="button" onClick={onClose}>닫기</button>
      </div>
    </form>
  </AgentDialog>
}
