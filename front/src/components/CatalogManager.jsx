import { useEffect, useRef, useState } from 'react'
import { assets, catalog, records, errorText } from '../api/index.js'
import { newKey } from '../api/client.js'
import AgentDialog from './AgentDialog.jsx'

const emptyOption = () => ({ name: '', size: '', color: '', sale_price: null, initial_quantity: null, produced_quantity: null, sold_quantity: null, replenishment_threshold: null })
const emptyProduct = () => ({ name: '', intent: '', material: '', process: '', photo_ids: [], cost: null, sale_price: null, price_estimated: true, quantity_date: null, options: [emptyOption()], status: 'draft' })
const productFields = ['name', 'intent', 'material', 'process', 'photo_ids', 'cost', 'sale_price', 'price_estimated', 'quantity_date', 'options', 'status']
const contactFields = ['name', 'category', 'contact', 'note', 'scope']

export default function CatalogManager({ projectId }) {
  const [kind, setKind] = useState('products')
  const [rows, setRows] = useState([])
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState(null)
  const [photos, setPhotos] = useState([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const pendingKey = `catalog-create:${projectId}:${kind}`
  const key = useRef(null)
  useEffect(() => { let active = true; catalog.list(projectId, kind).then((r) => { if (active) setRows(r) }).catch((e) => setError(errorText(e))); return () => { active = false } }, [projectId, kind])
  const edit = async (row) => {
    key.current = newKey(); setEditing(row); setError('')
    const pending = !row && JSON.parse(sessionStorage.getItem(pendingKey) || 'null')
    setUncertain(!!pending)
    const fields = kind === 'products' ? productFields : contactFields
    const values = row ? Object.fromEntries(fields.map((k) => [k, row[k]])) : kind === 'products' ? emptyProduct() : { name: '', category: '', contact: '', note: '', scope: 'project' }
    if (row && kind === 'products') values.options = row.options.map(({ inventory: _inventory, ...o }) => o)
    setForm(pending?.body || values)
    if (pending) key.current = pending.key
    try { setPhotos((await assets.list(projectId)).filter((a) => a.mime_type?.startsWith('image/'))) } catch (e) { setError(errorText(e)) }
  }
  const set = (field, value) => setForm((old) => ({ ...old, [field]: value }))
  const option = (i, field, value) => set('options', form.options.map((o, index) => index === i ? { ...o, [field]: value } : o))
  const save = async (status) => {
    setBusy(true); setError('')
    try {
      if (uncertain) {
        const result = await records.result(key.current)
        if (result.state === 'APPLIED') { sessionStorage.removeItem(pendingKey); setRows(await catalog.list(projectId, kind)); setForm(null); setUncertain(false) }
        else if (result.state === 'NOT_APPLIED') { sessionStorage.removeItem(pendingKey); setUncertain(false); key.current = newKey(); setError(result.error?.message || '저장하지 못했습니다.') }
        else setError('처리 결과를 확인 중입니다.')
        return
      }
      const body = { ...form, ...(kind === 'products' ? { status } : {}) }
      if (editing) await catalog.update(projectId, kind, editing.id, { ...body, revision: editing.revision })
      else { sessionStorage.setItem(pendingKey, JSON.stringify({ key: key.current, body })); await catalog.create(projectId, kind, body, key.current); sessionStorage.removeItem(pendingKey) }
      setRows(await catalog.list(projectId, kind)); setForm(null)
    } catch (e) { setError(errorText(e)); if (!editing && !uncertain) { if (e.status >= 400 && e.status < 500 && e.code !== 'REQUEST_IN_PROGRESS') { key.current = newKey(); sessionStorage.removeItem(pendingKey) } else setUncertain(true) } } finally { setBusy(false) }
  }
  const upload = async (file) => {
    if (!file) return
    setBusy(true)
    try { const a = await assets.upload(projectId, file, 'product_image'); setPhotos((old) => [...old, a]); set('photo_ids', [...form.photo_ids, a.id]) }
    catch (e) { setError(errorText(e)) } finally { setBusy(false) }
  }
  return <section className="card stack">
    <div className="row wrap"><h2>문서에 사용할 정보</h2><button type="button" onClick={() => setKind('products')}>제품</button><button type="button" onClick={() => setKind('contacts')}>거래처</button><button type="button" onClick={() => edit(null)}>추가</button></div>
    {error && !form && <p role="alert">{error}</p>}
    {rows.map((row) => <div className="row between" key={row.id}><span>{row.name || '이름 없는 제품'} {row.status === 'draft' ? '· 작성 중' : ''}{row.price_estimated ? ' · 예상 가격' : ''}</span><button type="button" onClick={() => edit(row)}>편집</button></div>)}
    {form && <AgentDialog title={kind === 'products' ? '제품 정보' : '거래처 정보'} onClose={() => setForm(null)}>
      <div className="stack">
        <fieldset className="stack" disabled={busy || uncertain}>
        {(kind === 'products' ? [['name', '제품명'], ['intent', '기획 의도'], ['material', '재료'], ['process', '제작 과정']] : [['name', '거래처명'], ['category', '분류'], ['contact', '연락처'], ['note', '기록']]).map(([field, label]) => <label key={field}>{label}<input value={form[field]} onChange={(e) => set(field, e.target.value)} /></label>)}
        {kind === 'contacts' ? <label>사용 범위<select value={form.scope} onChange={(e) => set('scope', e.target.value)}><option value="project">현재 프로젝트</option><option value="brand">브랜드 공통</option></select></label> : <>
          <fieldset><legend>제품 사진</legend>{photos.map((a) => <label className="agent-source" key={a.id}><input type="checkbox" checked={form.photo_ids.includes(a.id)} onChange={() => set('photo_ids', form.photo_ids.includes(a.id) ? form.photo_ids.filter((id) => id !== a.id) : [...form.photo_ids, a.id])} />{a.file_name}</label>)}<label>사진 올리기<input type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={(e) => upload(e.target.files[0])} /></label></fieldset>
          <div className="agent-options">{[['cost', '제작 원가(선택)'], ['sale_price', '기본 판매가']].map(([field, label]) => <label key={field}>{label} (원)<input type="number" min="0" step="1" value={form[field] ?? ''} onChange={(e) => set(field, e.target.value === '' ? null : Number(e.target.value))} /></label>)}</div>
          <label><input type="checkbox" checked={form.price_estimated} onChange={(e) => set('price_estimated', e.target.checked)} />예상 가격</label>
          <label>초기 수량 기준일<input type="date" disabled={!!editing?.quantity_initialized_at} value={form.quantity_date || ''} onChange={(e) => set('quantity_date', e.target.value || null)} /></label>
          {form.options.map((o, i) => <fieldset key={i}><legend>옵션 {i + 1}</legend><div className="agent-options">
            {[['name', '옵션명'], ['size', '크기'], ['color', '색상']].map(([field, label]) => <label key={field}>{label}<input value={o[field]} onChange={(e) => option(i, field, e.target.value)} /></label>)}
            {[['sale_price', '옵션 판매가(비우면 기본가)'], ['initial_quantity', '기존 수량'], ['produced_quantity', '제작 완료 수량'], ['sold_quantity', '기존 판매 수량'], ['replenishment_threshold', '재입고 기준 수량']].map(([field, label]) => <label key={field}>{label}<input type="number" min="0" step="1" disabled={!!editing?.quantity_initialized_at && ['initial_quantity', 'produced_quantity', 'sold_quantity'].includes(field)} value={o[field] ?? ''} onChange={(e) => option(i, field, e.target.value === '' ? null : Number(e.target.value))} /></label>)}
          </div>{!editing?.quantity_initialized_at && form.options.length > 1 && <button type="button" onClick={() => set('options', form.options.filter((_, n) => i !== n))}>옵션 삭제</button>}</fieldset>)}
          {!editing?.quantity_initialized_at && <button type="button" onClick={() => set('options', [...form.options, emptyOption()])}>옵션 추가</button>}
          <p>초기 수량은 최초 등록 시 한 번만 반영됩니다. 예상 가격으로도 등록할 수 있습니다.</p>
        </>}
        </fieldset>
        {error && <p role="alert" className="notice error">{error}</p>}
        <div className="agent-actions">{uncertain ? <button type="button" disabled={busy} onClick={() => save()}>원 요청 결과 확인</button> : <>{kind === 'products' && <button type="button" disabled={busy} onClick={() => save('draft')}>초안 저장</button>}<button type="button" disabled={busy} onClick={() => save('registered')}>{kind === 'products' ? '등록 완료' : '저장'}</button></>}</div>
      </div>
    </AgentDialog>}
  </section>
}
