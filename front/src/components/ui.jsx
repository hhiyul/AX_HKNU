// 여러 화면이 같이 쓰는 작은 조각.

export function Progress({ value, total }) {
  const percent = total ? Math.round((value / total) * 100) : 0
  return (
    <div className="progress-line">
      <div className="bar"><span style={{ width: `${percent}%` }} /></div>
      <small>확인한 필수 항목 {value}/{total}</small>
    </div>
  )
}

export const STATE_LABEL = { UNWRITTEN: '미작성', WRITING: '작성 중', REVIEWED: '검토 완료' }

export function StateBadge({ state }) {
  return <span className={`badge ${state}`}>{STATE_LABEL[state]}</span>
}

export const TEMPLATE_NAME = { funding: '펀딩 상세페이지', business_plan: '사업계획서' }

export function formatDate(value) {
  if (!value) return ''
  return new Date(value).toLocaleString('ko-KR', { year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
