import { useEffect, useState } from 'react'
import { records, errorText } from '../api/index.js'
import { newKey } from '../api/client.js'
import AgentDialog from './AgentDialog.jsx'

export default function RecordConfirmation({ projectId, message, onChanged }) {
  const storageKey = `record-confirm:${projectId}:${message.id}`
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState(message.record_draft?.title || '')
  const [content, setContent] = useState(message.record_draft?.content || '')
  const [operation, setOperation] = useState(() => sessionStorage.getItem(storageKey))
  const [saved, setSaved] = useState(message.record_key)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const resolve = (result) => {
    if (result.state === 'APPLIED') { setSaved(result.result.key); setOperation(null); sessionStorage.removeItem(storageKey); onChanged?.() }
    else if (result.state === 'NOT_APPLIED') { setOperation(null); sessionStorage.removeItem(storageKey); setError(result.error?.message || '저장하지 못했습니다. 내용을 확인해 주세요.') }
    else setError('저장 결과를 확인 중입니다. 새 저장 요청은 보내지 않습니다.')
  }
  useEffect(() => { if (operation) records.result(operation).then(resolve).catch((e) => setError(errorText(e))) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open || !saved) return
    let active = true
    records.get(projectId, saved).then((record) => { if (active) { setTitle(record.title); setContent(record.content) } }).catch((e) => { if (active) setError(errorText(e)) })
    return () => { active = false }
  }, [open, projectId, saved])
  if (!message.record_draft) return null
  const save = async () => {
    setBusy(true); setError('')
    const key = newKey(); setOperation(key); sessionStorage.setItem(storageKey, key)
    try { const record = await records.save(projectId, { title, content, message_id: message.id }, key); resolve({ state: 'APPLIED', result: record }) }
    catch (e) { setError(errorText(e)); if (e.status >= 400 && e.status < 500 && e.code !== 'REQUEST_IN_PROGRESS') { setOperation(null); sessionStorage.removeItem(storageKey) } }
    finally { setBusy(false) }
  }
  return <><button type="button" onClick={() => setOpen(true)}>{saved ? '저장한 기록 확인' : '기록 후보 확인'}</button>{open && <AgentDialog title="기록 후보" onClose={() => setOpen(false)}>
    <label>제목<input maxLength={200} value={title} disabled={!!operation || !!saved} onChange={(e) => setTitle(e.target.value)} /></label>
    <label>내용<textarea maxLength={5000} value={content} disabled={!!operation || !!saved} onChange={(e) => setContent(e.target.value)} /></label>
    {saved && <p role="status">기록으로 저장했습니다. 기획 화면에서 다시 편집할 수 있습니다.</p>}{error && <p role="alert">{error}</p>}
    {operation ? <button type="button" disabled={busy} onClick={() => records.result(operation).then(resolve).catch((e) => setError(errorText(e)))}>저장 결과 조회</button> : !saved && <button type="button" disabled={busy || !title.trim() || !content.trim()} onClick={save}>확인한 내용 저장</button>}
  </AgentDialog>}</>
}
