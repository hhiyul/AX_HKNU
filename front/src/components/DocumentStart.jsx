import { useEffect, useRef, useState } from 'react'
import { catalog, docs, records, errorText } from '../api/index.js'
import { newKey } from '../api/client.js'
import { go, paths } from '../router.js'
import AgentDialog from './AgentDialog.jsx'

export default function DocumentStart({ projectId, templateId = 'funding', message, onClose }) {
  const storageKey = `document-create:${projectId}:${message?.id || 'standalone'}`
  const [pending] = useState(() => JSON.parse(sessionStorage.getItem(storageKey) || 'null'))
  const [template, setTemplate] = useState(pending?.template || templateId)
  const [products, setProducts] = useState([])
  const [picked, setPicked] = useState(pending?.picked || [])
  const [useAnswer, setUseAnswer] = useState(pending?.useAnswer ?? !!message)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(!!pending)
  const key = useRef(pending?.key || newKey())
  useEffect(() => { let active = true; catalog.list(projectId, 'products').then((rows) => { if (active) setProducts(rows.filter((p) => p.status === 'registered')) }).catch((e) => setError(errorText(e))); return () => { active = false } }, [projectId])
  const create = async () => {
    setBusy(true)
    try {
      if (uncertain) {
        const result = await records.result(key.current)
        if (result.state === 'APPLIED') { sessionStorage.removeItem(storageKey); go(paths.doc(projectId, result.result.id)); onClose() }
        else if (result.state === 'NOT_APPLIED') { sessionStorage.removeItem(storageKey); setUncertain(false); key.current = newKey(); setError(result.error?.message || '문서를 만들지 못했습니다. 선택 내용을 확인해 주세요.') }
        else setError('아직 처리 결과가 확인되지 않았습니다.')
        return
      }
      sessionStorage.setItem(storageKey, JSON.stringify({ key: key.current, template, picked, useAnswer }))
      const doc = await docs.create(projectId, template, key.current, { product_ids: picked, ...(useAnswer && message ? { origin_message_id: message.id } : {}) })
      sessionStorage.removeItem(storageKey)
      go(paths.doc(projectId, doc.id)); onClose()
    } catch (e) { setError(errorText(e)); if (!uncertain) { setUncertain(!e.status || e.status >= 500 || e.code === 'REQUEST_IN_PROGRESS'); if (e.status >= 400 && e.status < 500 && e.code !== 'REQUEST_IN_PROGRESS') { key.current = newKey(); sessionStorage.removeItem(storageKey) } } } finally { setBusy(false) }
  }
  return <AgentDialog title="문서 시작" onClose={onClose}>
    <fieldset disabled={busy || uncertain}><legend>문서와 제품 선택</legend>
      <label>문서 종류<select value={template} onChange={(e) => setTemplate(e.target.value)}><option value="funding">펀딩 상세페이지</option><option value="business_plan">사업계획서</option></select></label>
      {products.map((p) => <label className="agent-source" key={p.id}><input type="checkbox" checked={picked.includes(p.id)} onChange={() => setPicked((ids) => ids.includes(p.id) ? ids.filter((id) => id !== p.id) : [...ids, p.id])} />{p.name} · {p.price_estimated ? '예상 ' : ''}{p.sale_price?.toLocaleString()}원</label>)}
      {!products.length && <p>등록 완료된 제품이 없습니다. 저장함에서 제품을 등록하거나 제품 없이 작성을 시작할 수 있습니다.</p>}
      {message && <label><input type="checkbox" checked={useAnswer} onChange={(e) => setUseAnswer(e.target.checked)} />이 답변 한 건을 첫 항목의 AI 참고로 선택</label>}
    </fieldset>
    {message && useAnswer && <details><summary>선택한 답변</summary><p>{message.content}</p></details>}
    <p>제품 정보는 문서 생성 시점의 값을 보관합니다. 선택한 답변은 항목 AI 도움에서 해제할 수 있습니다.</p>
    {error && <p role="alert">{error}</p>}
    {uncertain && <p>결과를 아직 확인하지 못했습니다. 같은 요청의 결과를 확인합니다.</p>}
    <button type="button" disabled={busy} onClick={create}>{uncertain ? '같은 요청 결과 확인' : '선택 내용으로 문서 만들기'}</button>
  </AgentDialog>
}
