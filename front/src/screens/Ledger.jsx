// 정산 · 수입·지출(프로토타입 정산 틀). 실제로 들어오고 나간 금액을 적는다. 원가·판매가격 비교와는 따로다.
import { useCallback, useEffect, useState } from 'react'
import { errorText, ledger } from '../api/index.js'

const TYPES = { income: '수입', expense: '지출' }
const won = (value) => `${Number(value || 0).toLocaleString('ko-KR')}원`
const today = () => new Date().toLocaleDateString('sv-SE')
const EMPTY = { type: 'income', title: '', amount: '', date: '', note: '' }

export default function Ledger({ projectId }) {
  const [data, setData] = useState(null)
  const [filter, setFilter] = useState('all')
  const [trashMode, setTrashMode] = useState(false)
  const [form, setForm] = useState(null)
  const [message, setMessage] = useState(null)

  const load = useCallback(() => ledger.list(projectId, trashMode ? 'trash' : 'active').then(setData)
    .catch((err) => setMessage({ error: errorText(err) })), [projectId, trashMode])
  useEffect(() => { load() }, [load])

  const open = (entry) => {
    setMessage(null)
    setForm(entry ? { ...entry, amount: String(entry.amount) } : { ...EMPTY, date: today() })
  }
  const submit = async (event) => {
    event.preventDefault()
    const body = { type: form.type, title: form.title.trim(), amount: Number(form.amount), date: form.date, note: form.note.trim() }
    try {
      if (form.id) await ledger.update(projectId, form.id, { ...body, revision: form.revision })
      else await ledger.create(projectId, body)
      setForm(null); setMessage({ ok: form.id ? '내역을 고쳤어요.' : '내역을 추가했어요.' })
      load()
    } catch (err) {
      setMessage({ error: err.status === 409 ? '다른 곳에서 먼저 바뀌었어요. 다시 열어 주세요.' : errorText(err) })
      if (err.status === 409) { setForm(null); load() }
    }
  }
  const remove = async (entry) => {
    if (!window.confirm(`"${entry.title}" 내역을 삭제할까요? 삭제한 내역에서 다시 꺼낼 수 있어요.`)) return
    try { await ledger.trash(projectId, entry.id); setMessage({ ok: '삭제한 내역으로 옮겼어요.' }); load() } catch (err) { setMessage({ error: errorText(err) }) }
  }
  const restore = async (entry) => {
    try { await ledger.restore(projectId, entry.id); setMessage({ ok: '내역을 복원했어요.' }); load() } catch (err) { setMessage({ error: errorText(err) }) }
  }
  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value })

  const entries = (data?.entries || []).filter((e) => filter === 'all' || e.type === filter)

  return (
    <>
      <div className="page-head row between wrap">
        <div>
          <h1>정산</h1>
          <p>실제로 들어오고 나간 금액을 기록해보세요.</p>
        </div>
        {!trashMode && <button type="button" className="primary" onClick={() => open(null)}>＋ 내역 추가</button>}
      </div>
      {message?.ok && <div className="notice" style={{ marginBottom: 16 }}>{message.ok}</div>}
      {message?.error && <div className="notice error" style={{ marginBottom: 16 }}>{message.error}</div>}

      {form && (
        <form className="card stack" style={{ gap: 12, marginBottom: 20 }} onSubmit={submit}>
          <h2>{form.id ? '정산 내역 수정' : '정산 내역 추가'}</h2>
          <div className="grid">
            <div>
              <label htmlFor="ledger-type">구분</label>
              <select id="ledger-type" value={form.type} onChange={set('type')}>
                <option value="income">수입</option><option value="expense">지출</option>
              </select>
            </div>
            <div>
              <label htmlFor="ledger-date">날짜</label>
              <input id="ledger-date" type="date" required value={form.date} onChange={set('date')} />
            </div>
          </div>
          <div>
            <label htmlFor="ledger-title">내역</label>
            <input id="ledger-title" required maxLength={100} placeholder="예: 팔찌 판매, 재료 구입" value={form.title} onChange={set('title')} />
          </div>
          <div>
            <label htmlFor="ledger-amount">금액 (원)</label>
            <input id="ledger-amount" type="number" inputMode="numeric" required min="1" max="1000000000000" step="1" value={form.amount} onChange={set('amount')} />
          </div>
          <div>
            <label htmlFor="ledger-note">메모 <span className="muted small">선택</span></label>
            <input id="ledger-note" maxLength={200} value={form.note} onChange={set('note')} />
          </div>
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" onClick={() => setForm(null)}>취소</button>
            <button className="primary">{form.id ? '저장' : '추가'}</button>
          </div>
        </form>
      )}

      {!trashMode && (
        <div className="work-money-summary">
          <div className="card"><span>수입</span><strong>{won(data?.income)}</strong></div>
          <div className="card"><span>지출</span><strong>{won(data?.expense)}</strong></div>
        </div>
      )}
      <div className="work-list-toolbar">
        {trashMode ? <strong>삭제한 내역</strong> : (
          <div className="work-segments">
            {[['all', '전체'], ['income', '수입'], ['expense', '지출']].map(([key, label]) => (
              <button key={key} type="button" aria-pressed={filter === key} onClick={() => setFilter(key)}>{label}</button>
            ))}
          </div>
        )}
        <button type="button" className="work-trash" onClick={() => { setTrashMode(!trashMode); setForm(null); setMessage(null) }}>
          {trashMode ? '← 내역으로' : '삭제한 내역'}
        </button>
      </div>
      <section className="work-record-list">
        {data && !entries.length && (
          <div className="card empty-state">
            <h2>{trashMode ? '삭제한 내역이 없어요.' : '아직 해당하는 정산 내역이 없어요.'}</h2>
            {!trashMode && <p className="muted">판매 수입이나 재료 구입비부터 적어보세요.</p>}
          </div>
        )}
        {entries.map((entry) => (
          <article key={entry.id} className="work-record">
            <div className="work-record-main">
              <span className={`work-money-type ${entry.type}`}>{TYPES[entry.type]}</span>
              <div>
                <strong>{entry.title}</strong>
                <p className="muted small">{entry.date}{entry.note ? ` · ${entry.note}` : ''}</p>
              </div>
            </div>
            <div className="work-record-actions">
              <strong>{entry.type === 'income' ? '+' : '−'}{won(entry.amount)}</strong>
              {trashMode ? <button type="button" onClick={() => restore(entry)}>복원</button> : (
                <>
                  <button type="button" onClick={() => open(entry)}>수정</button>
                  <button type="button" onClick={() => remove(entry)}>삭제</button>
                </>
              )}
            </div>
          </article>
        ))}
      </section>
    </>
  )
}
