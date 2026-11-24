// 정산(프로토타입 정산 틀): 수입·지출 | 원가·판매가격. 원가·판매가격(economics): 비용과 비교할 판매가를 넣으면 서버가 계산해 준다.
// 비교 기준은 "새 비교 기준으로 저장"을 누를 때만, 확정 가격은 "…로 결정"을 누를 때만 바뀐다.
import { useEffect, useRef, useState } from 'react'
import { economics, errorText } from '../api/index.js'
import { paths } from '../router.js'
import Ledger from './Ledger.jsx'

const COSTS = [['material', '재료비'], ['labor', '공임'], ['packaging', '포장비'], ['shipping', '배송비(판매자 부담)'], ['other', '기타 비용']]
const SAVE_DELAY_MS = 600
const won = (value) => (value === null || value === undefined ? '—' : `${Number(value).toLocaleString('ko-KR')}원`)
const signed = (value) => `${value > 0 ? '+' : ''}${Number(value).toLocaleString('ko-KR')}원`
const toNumber = (text) => (text === '' ? null : Number(text))

export default function Settlement({ projectId, page }) {
  const isCost = page === 'economics'
  return (
    <div className="workspace-page">
      <nav className="work-finance-nav" aria-label="정산 메뉴">
        <a href={`#${paths.settlement(projectId)}`} aria-current={isCost ? undefined : 'page'}>수입·지출</a>
        <a href={`#${paths.settlement(projectId)}/economics`} aria-current={isCost ? 'page' : undefined}>원가·판매가격</a>
      </nav>
      {isCost ? <Economics projectId={projectId} /> : <Ledger projectId={projectId} />}
    </div>
  )
}

function Economics({ projectId }) {
  const [data, setData] = useState(null)
  const [form, setForm] = useState({})
  const [message, setMessage] = useState(null)
  const timer = useRef(null)
  const revision = useRef(0)

  const apply = (view) => {
    setData(view); revision.current = view.revision
    setForm(Object.fromEntries(Object.entries(view.cost).map(([k, v]) => [k, v === null ? '' : String(v)])))
  }
  useEffect(() => { economics.get(projectId).then(apply).catch((err) => setMessage({ error: errorText(err) })) }, [projectId])
  useEffect(() => () => clearTimeout(timer.current), [])

  const save = async (next) => {
    try {
      const cost = Object.fromEntries(Object.entries(next).map(([k, v]) => [k, toNumber(v)]))
      const view = await economics.save(projectId, cost, revision.current)
      setData(view); revision.current = view.revision
    } catch (err) {
      setMessage({ error: err.status === 409 ? '다른 곳에서 먼저 바뀌어 최신 값을 불러왔어요.' : errorText(err) })
      if (err.status === 409) economics.get(projectId).then(apply)
    }
  }
  const change = (key, value) => {
    const next = { ...form, [key]: value }
    setForm(next); setMessage(null)
    clearTimeout(timer.current)
    timer.current = setTimeout(() => save(next), SAVE_DELAY_MS)
  }
  const act = async (work, okText) => {
    clearTimeout(timer.current)
    try { apply(await work()); setMessage({ ok: okText }) } catch (err) { setMessage({ error: errorText(err) }) }
  }

  if (!data) return message?.error ? <div className="notice error">{message.error}</div> : <p className="muted">불러오는 중…</p>
  const { before, keep, change: changed } = data

  return (
    <>
      <div className="page-head row between wrap">
        <div>
          <h1>원가·판매가격</h1>
          <p>이 가격에 팔면 얼마나 남을까요? 가격을 바꿨을 때도 함께 비교해 보세요.</p>
        </div>
      </div>
      {message?.ok && <div className="notice" style={{ marginBottom: 16 }}>{message.ok}</div>}
      {message?.error && <div className="notice error" style={{ marginBottom: 16 }}>{message.error}</div>}

      {keep && (
        <div className="econ-summary row between wrap">
          <span><strong>개당 비용 {before ? `${won(before.cost)} → ` : ''}{won(keep.cost)}</strong>
            {before && keep.cost !== before.cost && <span className={keep.cost > before.cost ? 'negative' : 'positive'}> ({signed(keep.cost - before.cost)})</span>}</span>
          <span>기존 판매가 유지 시 {won(keep.net)} 남아요.</span>
        </div>
      )}
      {(before || keep || changed) && <p className="mobile-comparison-hint">옆으로 넘겨 가격에 따른 차이를 살펴보세요.</p>}
      <section className="econ-compare mono">
        <Card kind="before" step="01 / 변경 전 기준" title="비용이 바뀌기 전" sub="저장해 둔 비용과 판매가" result={before}
          empty="아래에서 비용과 판매가를 넣고 비교 기준으로 저장해 보세요." note="비교의 출발점 · 자동으로 바뀌지 않아요." />
        <Card kind="keep" step="02 / 기존 판매가 유지" title="같은 가격에 계속 팔면" sub="지금 비용 + 기존 판매가" result={keep}
          empty={data.confirmed_price || before ? '비용을 모두 넣으면 계산돼요.' : '정한 판매가가 아직 없어요.'}
          action={keep && <button type="button" onClick={() => act(() => economics.confirm(projectId, 'keep', revision.current), '기존 판매가로 결정했어요. 상품기획의 가격결정에 반영했어요.')}>기존 판매가로 결정</button>} />
        <Card kind="change" step="03 / 판매가 변경" title="가격을 바꿔서 팔면" sub="지금 비용 + 내가 입력한 판매가" result={changed}
          empty="비교할 판매가를 넣으면 계산돼요." extra={changed && keep ? changed.net - keep.net : null}
          action={changed && <button type="button" className="primary" onClick={() => act(() => economics.confirm(projectId, 'change', revision.current), '이 판매가로 결정했어요. 상품기획의 가격결정에 반영했어요.')}>이 판매가로 결정</button>} />
      </section>

      <div className="economic-inputs">
        <section className="card">
          <div className="row between wrap"><h2>만드는 데 얼마가 드나요?</h2><span className="badge">상품 1개 기준</span></div>
          <p className="help">모르는 값은 비워 두세요. 비용이 없으면 0을 넣어요.</p>
          {COSTS.map(([key, label]) => (
            <div key={key} className="cost-row">
              <div>
                <label htmlFor={`cost-${key}`}>{label} <span className="muted small">원</span></label>
                {data.base && <span className="baseline-hint">기준 {won(data.base[key])}</span>}
              </div>
              <input id={`cost-${key}`} type="number" inputMode="numeric" min="0" value={form[key] ?? ''} onChange={(e) => change(key, e.target.value)} />
            </div>
          ))}
          <div className="cost-row">
            <div><label htmlFor="cost-fee">판매 수수료 <span className="muted small">%</span></label></div>
            <input id="cost-fee" type="number" inputMode="decimal" min="0" max="100" step="0.1" value={form.fee_rate ?? ''} onChange={(e) => change('fee_rate', e.target.value)} />
          </div>
          <div className="divider" />
          <button type="button" className="small" onClick={() => act(() => economics.baseline(projectId, revision.current), '지금 입력을 새 비교 기준으로 저장했어요.')}>현재 입력을 새 비교 기준으로 저장</button>
          <p className="help">비교 기준은 이 버튼을 누를 때만 바뀝니다.</p>
        </section>
        <section className="card">
          <h2>얼마에 팔아볼까요?</h2>
          <p className="help">내가 정한 판매가: {won(data.confirmed_price)} · 다른 가격을 비교해도 정한 판매가는 바뀌지 않아요.</p>
          <div className="form-field">
            <label htmlFor="cost-price">비교할 판매가 <span className="muted small">원</span></label>
            <input id="cost-price" className="price-candidate mono" type="number" inputMode="numeric" min="0" value={form.price ?? ''} onChange={(e) => change('price', e.target.value)} />
          </div>
          {data.candidates.length > 0 && (
            <>
              <h3>다른 금액도 비교해 보기</h3>
              <table className="scenario mono">
                <thead><tr><th>판매가</th><th>수수료</th><th>남는 금액</th></tr></thead>
                <tbody>
                  {data.candidates.map((c) => (
                    <tr key={c.price}><td>{won(c.price)}</td><td>{won(c.fee)}</td><td className={c.net >= 0 ? 'positive' : 'negative'}>{won(c.net)}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          <p className="help">세금·입력하지 않은 비용은 포함되지 않아요.</p>
        </section>
      </div>
    </>
  )
}

function Card({ kind, step, title, sub, result, empty, note, extra, action }) {
  return (
    <article className={`card comparison-card ${kind}`}>
      <div className="eyebrow">{step}</div>
      <h2>{title}</h2>
      <p className="muted small">{sub}</p>
      {result ? (
        <>
          <div className="comparison-price"><span>판매가</span><strong>{won(result.price)}</strong></div>
          <div className="comparison-breakdown">
            <div><span>개당 비용</span><span>{won(result.cost)}</span></div>
            <div><span>판매 수수료</span><span>{won(result.fee)}</span></div>
          </div>
          <div className="comparison-net"><span>한 개 판매 후 남는 금액</span><strong>{won(result.net)}</strong></div>
          {note && <div className="comparison-delta muted">{note}</div>}
          {result.delta !== null && result.delta !== undefined && (
            <div className={`comparison-delta ${result.delta >= 0 ? 'positive' : 'negative'}`}>변경 전보다 {signed(result.delta)} / 개</div>
          )}
          {extra !== null && extra !== undefined && <p className="comparison-extra">가격 유지보다 / 개 <strong>{signed(extra)}</strong></p>}
          {action}
        </>
      ) : <p className="muted small" style={{ marginTop: 16 }}>{empty}</p>}
    </article>
  )
}
