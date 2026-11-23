// 프로젝트를 고르기 전 화면(프로토타입 빈 작업실) + 계정 전체의 오늘 이어서 할 일(기획서 v2 계정 홈).
import { useEffect, useState } from 'react'
import { errorText, home } from '../api/index.js'
import { go, paths } from '../router.js'
import { Progress, TEMPLATE_NAME } from '../components/ui.jsx'

function openTarget(projectId, docId, sectionId) {
  if (docId) go(paths.doc(projectId, docId), sectionId ? { s: sectionId } : undefined)
  else go(paths.tasks(projectId))
}

export default function Home() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => { home.get().then(setData).catch((err) => setError(errorText(err))) }, [])

  const hasWork = data && (data.recent_docs.length || data.today_tasks.length || data.overdue_tasks.length || data.recommendations.length)

  return (
    <div className="workspace-page">
      <section className="workspace-placeholder">
        <div className="workspace-placeholder-icon">↖</div>
        <h1>어떤 프로젝트를 이어갈까요?</h1>
        <p>왼쪽에서 프로젝트를 고르거나 새 프로젝트를 만들어 보세요.</p>
      </section>
      {error && <div className="notice error">{error}</div>}
      {hasWork ? (
        <div className="dashboard-columns" style={{ marginTop: 24 }}>
          <section className="dashboard-panel">
            <header className="dashboard-panel-head"><div><h2>오늘 이어서 할 일</h2><p>모든 프로젝트에서 모았어요.</p></div></header>
            {!data.recommendations.length && !data.today_tasks.length && <p className="dashboard-empty">지금은 추천할 작업이 없어요.</p>}
            <ul className="home-list">
              {data.today_tasks.map((t) => (
                <li key={t.task_id}><button type="button" onClick={() => openTarget(t.project_id, t.source_doc_id, t.source_section_id)}>
                  <strong>{t.title}</strong><small>오늘 · {t.project_name}</small>
                </button></li>
              ))}
              {data.recommendations.map((r, index) => (
                <li key={index}><button type="button" onClick={() => openTarget(r.project_id, r.doc_id, r.section_id)}>
                  <strong>{r.title}</strong><small>추천 작업</small>
                </button></li>
              ))}
            </ul>
          </section>
          <section className="dashboard-panel">
            <header className="dashboard-panel-head"><div><h2>최근 작성한 문서</h2><p>마지막으로 쓰던 항목에서 열려요.</p></div></header>
            {!data.recent_docs.length && <p className="dashboard-empty">아직 작성한 문서가 없어요.</p>}
            <ul className="home-list">
              {data.recent_docs.map((d) => (
                <li key={d.doc_id}><button type="button" onClick={() => openTarget(d.project_id, d.doc_id, d.last_section_id)}>
                  <strong>{d.title}</strong>
                  <small>{d.project_name} · {TEMPLATE_NAME[d.template_id]}{d.last_section_title ? ` · 마지막: ${d.last_section_title}` : ''}</small>
                  <Progress value={d.progress.required_reviewed} total={d.progress.required_total} />
                </button></li>
              ))}
            </ul>
          </section>
        </div>
      ) : null}
    </div>
  )
}
