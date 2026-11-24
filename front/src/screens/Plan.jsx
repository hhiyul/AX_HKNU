// 상품기획(프로토타입 "프로젝트 기본 기획" 틀). 6항목을 보고 "변경"으로 고친다. 비워 두면 미작성이다.
import { useCallback, useEffect, useState } from 'react'
import { errorText, plan } from '../api/index.js'
import { go, paths } from '../router.js'

export default function Plan({ projectId }) {
  const [items, setItems] = useState([])
  const [editing, setEditing] = useState(null)
  const [draft, setDraft] = useState('')
  const [message, setMessage] = useState(null)
  const load = useCallback(() => plan.list(projectId).then(setItems).catch((err) => setMessage({ error: errorText(err) })), [projectId])
  useEffect(() => { load() }, [load])

  const start = (item) => { setEditing(item.key); setDraft(item.content || ''); setMessage(null) }
  const save = async (item) => {
    try {
      await plan.save(projectId, item.key, draft, item.revision)
      setEditing(null)
      setMessage({ ok: `${item.title}을(를) 저장했어요.` })
      load()
    } catch (err) {
      setMessage({ error: errorText(err) })
      load()
    }
  }

  return (
    <div className="workspace-page">
      <div className="work-dashboard planning-dashboard">
        <div className="page-head">
          <h1>상품기획</h1>
          <p>내 상품에 대한 생각을 하나씩 모아보세요. 문서를 쓸 때 참고 정보로 고를 수 있어요.</p>
        </div>
        <section className="card">
          <div className="row between wrap">
            <h2>프로젝트 기본 기획</h2>
            <button type="button" className="small" onClick={() => go(paths.project(projectId))}>작업실로</button>
          </div>
          <p className="muted">처음 적어 둔 생각이에요. 달라진 마음이나 새로 떠오른 생각이 있으면 바꿔 주세요.</p>
          {message?.ok && <div className="notice" style={{ marginTop: 12 }}>{message.ok}</div>}
          {message?.error && <div className="notice error" style={{ marginTop: 12 }}>{message.error}</div>}
          {items.map((item, index) => (
            <div key={item.key} className="info-row plan-edit">
              <div className="row between">
                <h3>{index + 1}. {item.title}</h3>
                <div className="row">
                  <span className={`badge ${item.state === 'WRITTEN' ? 'saved' : ''}`}>{item.state === 'WRITTEN' ? '작성됨' : '미작성'}</span>
                  {editing !== item.key && <button type="button" className="small" onClick={() => start(item)}>{item.content ? '변경' : '쓰기'}</button>}
                </div>
              </div>
              {editing === item.key ? (
                <>
                  <textarea aria-label={item.title} autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} />
                  <div className="row plan-buttons">
                    <button type="button" className="small" onClick={() => setEditing(null)}>취소</button>
                    <button type="button" className="primary small" onClick={() => save(item)}>저장</button>
                  </div>
                </>
              ) : (
                <p className={item.content ? '' : 'muted'}>{item.content || '아직 적지 않았어요.'}</p>
              )}
            </div>
          ))}
        </section>
      </div>
    </div>
  )
}
