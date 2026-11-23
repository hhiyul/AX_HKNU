// 프로젝트 작업실(프로토타입 대시보드 틀): 다음에 할 일 · 숫자 칸 · 일정 · 작성 중인 문서 · 최근 작업.
import { useEffect, useState } from 'react'
import { docs, errorText, home, projects, tasks } from '../api/index.js'
import { go, paths } from '../router.js'
import { formatDate, Progress, TEMPLATE_NAME } from '../components/ui.jsx'

const STAGES = [['planning', '기획'], ['prototype', '시제품'], ['ready', '판매 준비'], ['selling', '판매 중']]
const today = () => new Date().toLocaleDateString('sv-SE')

function openTarget(projectId, docId, sectionId) {
  if (docId) go(paths.doc(projectId, docId), sectionId ? { s: sectionId } : undefined)
  else go(paths.tasks(projectId))
}

export default function Workroom({ projectId }) {
  const [project, setProject] = useState(null)
  const [docList, setDocList] = useState([])
  const [taskList, setTaskList] = useState([])
  const [next, setNext] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([projects.get(projectId), docs.list(projectId), tasks.list(projectId), home.get()])
      .then(([p, d, t, h]) => {
        setProject(p); setDocList(d); setTaskList(t)
        setNext(h.recommendations.find((r) => r.project_id === projectId) || null)
      })
      .catch((err) => setError(errorText(err)))
  }, [projectId])
  // 옆 패널에서 메모를 저장하면 여기 메모 칸도 바로 바뀐다.
  useEffect(() => {
    const onUpdated = (event) => { if (event.detail?.id === projectId) setProject(event.detail) }
    window.addEventListener('ax:project-updated', onUpdated)
    return () => window.removeEventListener('ax:project-updated', onUpdated)
  }, [projectId])

  if (error) return <div className="workspace-page"><div className="notice error">{error}</div></div>
  if (!project) return <div className="workspace-page"><p className="muted">불러오는 중…</p></div>

  const now = today()
  const open = taskList.filter((t) => !t.done)
  const todayTasks = open.filter((t) => t.date === now)
  const upcoming = open.filter((t) => t.date > now).sort((a, b) => a.date.localeCompare(b.date)).slice(0, 5)
  const overdue = open.filter((t) => t.date < now)
  const stageIndex = STAGES.findIndex(([key]) => key === project.stage)
  const reviewed = docList.reduce((sum, d) => sum + d.progress.required_reviewed, 0)
  const required = docList.reduce((sum, d) => sum + d.progress.required_total, 0)

  return (
    <div className="workspace-page">
      <div className="work-dashboard studio-dashboard">
        <header className="dashboard-heading">
          <div>
            <h1>작업실</h1>
            <p>진행 상황을 살펴보고, 오늘의 작업을 이어가세요.</p>
          </div>
        </header>

        <section className="dashboard-next">
          <div className="dashboard-next-top">
            <span className="dashboard-project-name">{project.name}</span>
            <span className="dashboard-stage-badge">{STAGES[stageIndex]?.[1] || '기획'}</span>
          </div>
          <div className="dashboard-next-body">
            <div>
              <span className="dashboard-kicker">다음에 할 일</span>
              <h2>{next?.title || project.next_action || (docList.length ? '작성 중인 문서를 이어서 써 보세요.' : '펀딩 상세페이지나 사업계획서를 시작해 보세요.')}</h2>
              <p>{next ? '항목을 쓰고 검토 완료를 누르면 진행률이 올라가요.' : '저장함에서 새 문서를 만들 수 있어요.'}</p>
            </div>
            <button type="button" className="dashboard-primary"
              onClick={() => (next ? openTarget(projectId, next.doc_id, next.section_id) : go(paths.docs(projectId)))}>
              {next ? '이어서 하기' : '저장함 열기'}
            </button>
          </div>
          <ol className="dashboard-stage-track" aria-label="프로젝트 진행 단계">
            {STAGES.map(([key, label], index) => (
              <li key={key} aria-current={index === stageIndex ? 'step' : undefined}><span aria-hidden="true" />{label}</li>
            ))}
          </ol>
        </section>

        <section className="dashboard-stats">
          <button type="button" className="dashboard-stat" onClick={() => go(paths.plan(projectId))}>
            <span className="dashboard-stat-label">기본 기획</span>
            <strong className="dashboard-stat-value">{project.plan_filled}<span> / 6</span></strong>
            <span className="dashboard-stat-hint">{project.plan_filled === 6 ? '기본 기획을 모두 적었어요' : '눌러서 기본 기획을 적어 보세요'}</span>
          </button>
          <button type="button" className="dashboard-stat" onClick={() => go(paths.tasks(projectId))}>
            <span className="dashboard-stat-label">미완료 일정</span>
            <strong className="dashboard-stat-value">{open.length}<span>건</span></strong>
            <span className="dashboard-stat-hint">앞으로 할 일을 확인하세요</span>
          </button>
          <button type="button" className="dashboard-stat" onClick={() => go(paths.docs(projectId))}>
            <span className="dashboard-stat-label">작성 중인 문서</span>
            <strong className="dashboard-stat-value">{docList.length}<span>개</span></strong>
            <span className="dashboard-stat-hint">저장함에서 이어서 쓸 수 있어요</span>
          </button>
          <button type="button" className="dashboard-stat" onClick={() => go(paths.docs(projectId))}>
            <span className="dashboard-stat-label">확인한 필수 항목</span>
            <strong className="dashboard-stat-value">{reviewed}<span> / {required}</span></strong>
            <span className="dashboard-stat-hint">검토 완료한 항목 수예요</span>
          </button>
        </section>

        <div className="dashboard-columns">
          <section className="dashboard-panel dashboard-schedule">
            <header className="dashboard-panel-head">
              <div><h2>오늘과 다가오는 일정</h2><p>날짜별로 해야 할 일을 살펴보세요.</p></div>
              <button type="button" onClick={() => go(paths.tasks(projectId))}>일정 보기</button>
            </header>
            <TaskGroup title="오늘 할 일" items={todayTasks} empty="오늘 예정된 일이 없어요." projectId={projectId} />
            <TaskGroup title="다가오는 일정" items={upcoming} empty="다음 제작·검수·판매 날짜를 정해보세요." projectId={projectId} />
            <TaskGroup title="기한이 지난 일정" items={overdue} empty="기한이 지난 미완료 일정이 없어요." projectId={projectId} />
          </section>

          <section className="dashboard-panel dashboard-checks">
            <header className="dashboard-panel-head">
              <div><h2>작성 중인 문서</h2><p>마지막으로 쓰던 항목에서 이어가요.</p></div>
              <button type="button" onClick={() => go(paths.docs(projectId))}>모두 보기</button>
            </header>
            {!docList.length && <p className="dashboard-empty">아직 문서가 없어요. 저장함에서 시작해 보세요.</p>}
            <ul className="home-list">
              {docList.map((d) => (
                <li key={d.id}>
                  <button type="button" onClick={() => openTarget(projectId, d.id, d.last_section_id)}>
                    <strong>{d.title}</strong>
                    <small>{TEMPLATE_NAME[d.template_id]}{d.last_section_title ? ` · 마지막: ${d.last_section_title}` : ''}</small>
                    <Progress value={d.progress.required_reviewed} total={d.progress.required_total} />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>

        <div className="dashboard-columns dashboard-bottom">
          <section className="card studio-fact studio-resume">
            <h2>최근 작업</h2>
            <ul className="studio-recent-facts">
              {(project.recent || []).slice(0, 5).map((r, index) => (
                <li key={index}><span>{r.text}</span> <small className="muted">{formatDate(r.at)}</small></li>
              ))}
              {!project.recent?.length && <li className="muted">아직 기록이 없어요.</li>}
            </ul>
          </section>
          <section className="card studio-fact studio-resume studio-memo">
            <div className="row between">
              <h2>메모</h2>
              <button type="button" className="small" onClick={() => window.dispatchEvent(new Event('ax:open-memo'))}>메모 수정</button>
            </div>
            <p className="studio-memo-text">{project.note || '프로젝트 메모가 아직 없어요.'}</p>
          </section>
        </div>
      </div>
    </div>
  )
}

function TaskGroup({ title, items, empty, projectId }) {
  return (
    <div className="dashboard-schedule-group">
      <h3>{title} <span className="muted">{items.length}</span></h3>
      {!items.length && <p className="dashboard-empty">{empty}</p>}
      <ul className="home-list">
        {items.map((t) => (
          <li key={t.id}>
            <button type="button" onClick={() => openTarget(projectId, t.source?.doc_status === 'active' ? t.source.doc_id : null, t.source?.section_id)}>
              <strong>{t.title}</strong>
              <small>{t.date}{t.source?.section_title ? ` · ${t.source.section_title}` : ''}</small>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
