// 저장함(프로토타입 문서 카드 틀): 새 문서 시작(두 작성 틀) + 이 프로젝트의 문서.
import { useEffect, useState } from 'react'
import { docs, errorText } from '../api/index.js'
import DocumentStart from '../components/DocumentStart.jsx'
import CatalogManager from '../components/CatalogManager.jsx'
import { go, paths } from '../router.js'
import Materials from '../components/Materials.jsx'
import { formatDate, Progress } from '../components/ui.jsx'

const TEMPLATES = [
  ['funding', '펀딩 상세페이지', '작품 소개부터 리워드·제작 계획까지 8개 항목(필수 6)'],
  ['business_plan', '사업계획서', '사업 개요부터 성과·검증 계획까지 8개 항목(필수 8)'],
]

export default function Docs({ projectId }) {
  const [list, setList] = useState(null)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(null)
  // 같은 버튼을 여러 번 눌러도 문서는 하나만 생기게 틀마다 같은 Idempotency-Key를 쓴다.

  useEffect(() => {
    docs.list(projectId).then(setList).catch((err) => setError(errorText(err)))
  }, [projectId])

  return (
    <div className="workspace-page">
      <div className="page-head row between wrap">
        <div>
          <h1>저장함</h1>
          <p>내가 만든 문서와 첨부한 자료가 모여 있어요.</p>
        </div>
      </div>
      {error && <div className="notice error">{error}</div>}

      <section className="card" style={{ marginBottom: 24 }}>
        <h2>새 문서 시작</h2>
        <p className="muted small" style={{ margin: '4px 0 14px' }}>작성 틀을 고르면 항목이 미리 나뉘어 있어요.</p>
        <div className="doc-new">
          {TEMPLATES.map(([id, name, desc]) => (
            <button key={id} type="button" onClick={() => setStarting(id)}>
              <strong>＋ {name}</strong><span>{desc}</span>
            </button>
          ))}
        </div>
      </section>

      <div className="row between"><span className="muted small">문서 {list?.length ?? 0}개 · 최근 저장 순</span></div>
      {list?.length === 0 && <p className="muted" style={{ marginTop: 12 }}>아직 문서가 없어요.</p>}
      <section className="doc-grid">
        {list?.map((doc) => (
          <article key={doc.id} className="doc-card">
            <button type="button" className="doc-open"
              onClick={() => go(paths.doc(projectId, doc.id), doc.last_section_id ? { s: doc.last_section_id } : undefined)}>
              <div className="doc-thumbnail">
                <div className="row between"><span className="doc-type">DOCUMENT</span></div>
                <div className="doc-paper">
                  <strong>{doc.title}</strong>
                  <p>{doc.last_section_title ? `마지막 작업: ${doc.last_section_title}` : '아직 쓴 항목이 없어요'}</p>
                </div>
              </div>
              <div className="doc-meta">
                <h2>{doc.title}</h2>
                <Progress value={doc.progress.required_reviewed} total={doc.progress.required_total} />
                <p className="muted small">{formatDate(doc.updated_at)}</p>
              </div>
            </button>
          </article>
        ))}
      </section>
      <div className="gap" />
      <Materials projectId={projectId} />
      <CatalogManager projectId={projectId} />
      {starting && <DocumentStart projectId={projectId} templateId={starting} onClose={() => setStarting(null)} />}
    </div>
  )
}
