// 일정(프로토타입 일정 달력 틀): 달력 + 고른 날의 할 일. 문서 항목에서 만든 일정은 "항목으로" 돌아간다.
// 완료 체크는 문서 검토와 따로다.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { errorText, tasks } from '../api/index.js'
import { go, paths } from '../router.js'

const WEEK = ['일', '월', '화', '수', '목', '금', '토']
const iso = (date) => date.toLocaleDateString('sv-SE')
const todayIso = () => iso(new Date())

function monthCells(year, month) {
  const first = new Date(year, month, 1)
  const days = new Date(year, month + 1, 0).getDate()
  return [
    ...Array.from({ length: first.getDay() }, () => null),
    ...Array.from({ length: days }, (_, i) => iso(new Date(year, month, i + 1))),
  ]
}

export default function Tasks({ projectId }) {
  const [list, setList] = useState([])
  const [selected, setSelected] = useState(todayIso)
  const [month, setMonth] = useState(() => { const d = new Date(); return { y: d.getFullYear(), m: d.getMonth() } })
  // 폰에서는 날짜를 눌렀을 때만 그날 패널을 연다(프로토타입과 같음). PC는 처음부터 연다.
  const [panelOpen, setPanelOpen] = useState(() => window.matchMedia('(min-width: 701px)').matches)
  const [adding, setAdding] = useState(false)
  const [title, setTitle] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(() => tasks.list(projectId).then(setList).catch((err) => setError(errorText(err))), [projectId])
  useEffect(() => { load() }, [load])

  const byDate = useMemo(() => list.reduce((map, t) => ({ ...map, [t.date]: [...(map[t.date] || []), t] }), {}), [list])
  const cells = monthCells(month.y, month.m)
  const now = todayIso()
  const dayTasks = byDate[selected] || []

  const shift = (delta) => { const d = new Date(month.y, month.m + delta, 1); setMonth({ y: d.getFullYear(), m: d.getMonth() }) }
  const goToday = () => { const d = new Date(); setMonth({ y: d.getFullYear(), m: d.getMonth() }); setSelected(iso(d)) }
  const toggle = (task) => tasks.update(projectId, task.id, { done: !task.done, revision: task.revision }).then(load).catch((err) => setError(errorText(err)))
  const add = async (event) => {
    event.preventDefault()
    if (!title.trim()) return
    try {
      await tasks.create(projectId, { title: title.trim(), date: selected })
      setTitle(''); setAdding(false); setError('')
      load()
    } catch (err) {
      setError(errorText(err))
    }
  }
  const [y, m, d] = selected.split('-')

  return (
    <div className="work-schedule-layout">
      <section className="card work-calendar schedule-calendar">
        <div className="work-calendar-head">
          <div className="work-calendar-heading">
            <h2>일정 달력</h2>
            <button type="button" onClick={goToday}>오늘</button>
          </div>
          <div className="work-calendar-nav">
            <button type="button" aria-label="이전 달" onClick={() => shift(-1)}>‹</button>
            <strong>{month.y}년 {month.m + 1}월</strong>
            <button type="button" aria-label="다음 달" onClick={() => shift(1)}>›</button>
          </div>
          <div className="work-calendar-actions">
            <button type="button" className="work-calendar-add" onClick={() => { setAdding(true); setPanelOpen(true) }}>＋ 일정 추가</button>
          </div>
        </div>
        <div className="work-calendar-week">{WEEK.map((w) => <span key={w}>{w}</span>)}</div>
        <div className="work-calendar-grid">
          {cells.map((date, index) => {
            if (!date) return <span key={`b${index}`} className="work-calendar-blank" />
            const items = byDate[date] || []
            return (
              <button key={date} type="button" className={`work-calendar-day${date === now ? ' is-today' : ''}`}
                aria-pressed={date === selected} aria-label={`${date}${date === now ? ' 오늘' : ''}, 일정 ${items.length}개`}
                onClick={() => { setSelected(date); setAdding(false); setPanelOpen(true) }}>
                <span className="work-calendar-number">{Number(date.slice(8))}</span>
                {items.length > 0 && (
                  <>
                    <span className="work-calendar-events">
                      {items.slice(0, 3).map((t) => <span key={t.id} className={`task-color-blue${t.done ? ' is-done' : ''}`}>{t.title}</span>)}
                      {items.length > 3 && <small>+{items.length - 3}</small>}
                    </span>
                    <span className="work-calendar-dot" aria-hidden="true">{items.length}</span>
                  </>
                )}
              </button>
            )
          })}
        </div>
      </section>

      <aside className="work-day-panel" aria-label="선택한 날짜의 할 일" hidden={!panelOpen}>
        <div className="work-day-panel-head">
          <h2>{`${y}. ${Number(m)}. ${Number(d)}`}{selected === now ? ' · 오늘' : ''}</h2>
          <button type="button" aria-label="날짜 패널 닫기" onClick={() => setPanelOpen(false)}>×</button>
        </div>
        {error && <div className="notice error">{error}</div>}
        {adding ? (
          <form className="stack" style={{ gap: 8 }} onSubmit={add}>
            <label htmlFor="task-title" className="sr-only">할 일</label>
            <input id="task-title" autoFocus required maxLength={100} placeholder="예: 팔찌 시제품 검수" value={title} onChange={(e) => setTitle(e.target.value)} />
            <div className="row">
              <button type="button" className="small" onClick={() => setAdding(false)}>취소</button>
              <button className="primary small">이날에 추가</button>
            </div>
          </form>
        ) : (
          <button type="button" className="work-day-add" onClick={() => setAdding(true)}>＋ 일정 추가</button>
        )}
        <div className="work-day-tasks">
          {!dayTasks.length && <p className="work-calendar-empty">이날 예정된 일정이 없어요.</p>}
          {dayTasks.map((task) => (
            <article key={task.id} className={`work-calendar-task${task.done ? ' completed' : ''}`}>
              <input type="checkbox" aria-label={`${task.title} 완료`} checked={task.done} onChange={() => toggle(task)} />
              <div>
                <strong className="work-task-label task-color-blue">{task.title}</strong>
                {task.note && <p>{task.note}</p>}
                {task.source?.section_title && <p>{task.source.doc_title} · {task.source.section_title}</p>}
              </div>
              {task.source?.doc_id && task.source.doc_status === 'active' && (
                <button type="button" onClick={() => go(paths.doc(projectId, task.source.doc_id), { s: task.source.section_id })}>항목으로</button>
              )}
            </article>
          ))}
        </div>
      </aside>
    </div>
  )
}
