// 프로토타입 작업실 틀: 왼쪽(브랜드 전환·프로젝트 목록, 폰에서는 ☰ 서랍) + 위쪽 작업 탭·메모 버튼 + 본문 + 메모·기록 패널.
import { useEffect, useState } from 'react'
import { brands, errorText, projects } from '../api/index.js'
import { go, paths } from '../router.js'
import MemoPanel from './MemoPanel.jsx'

export const TABS = [['home', '작업실'], ['chat', 'AI협업'], ['tasks', '일정'], ['settlement', '정산'], ['docs', '저장함']]
const ALL = 'all'
const NONE = 'none'
const BRAND_KEY = 'ax.brand'

function readBrand() {
  try { return localStorage.getItem(BRAND_KEY) || ALL } catch { return ALL }
}
function writeBrand(value) {
  try { localStorage.setItem(BRAND_KEY, value) } catch { /* 저장이 막혀 있으면 이번 화면에서만 쓴다 */ }
}
const wideScreen = () => window.matchMedia('(min-width: 1051px)').matches

export default function Shell({ user, projectId, tab, onLogout, children }) {
  const [list, setList] = useState([])
  const [brandList, setBrandList] = useState([])
  const [brand, setBrand] = useState(readBrand)
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [memoOpen, setMemoOpen] = useState(wideScreen)
  const [adding, setAdding] = useState(null)
  const [name, setName] = useState('')
  const [error, setError] = useState('')

  const loadProjects = () => projects.list(brand === ALL ? undefined : brand).then(setList).catch(() => setList([]))
  useEffect(() => { loadProjects() }, [projectId, brand]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { brands.list().then(setBrandList).catch(() => setBrandList([])) }, [])
  useEffect(() => { setOpen(false) }, [projectId, tab])
  useEffect(() => {
    const openMemo = () => setMemoOpen(true)
    window.addEventListener('ax:open-memo', openMemo)
    return () => window.removeEventListener('ax:open-memo', openMemo)
  }, [])
  useEffect(() => {
    if (!open) return undefined
    const onKey = (event) => { if (event.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const pickBrand = (value, event) => {
    setBrand(value); writeBrand(value)
    event.currentTarget.closest('details')?.removeAttribute('open')
  }
  const submit = async (event) => {
    event.preventDefault()
    if (!name.trim()) return
    try {
      if (adding === 'brand') {
        const created = await brands.create(name.trim())
        setBrandList([...brandList, created]); setBrand(created.id); writeBrand(created.id)
      } else {
        const project = await projects.create(name.trim(), null, brand !== ALL && brand !== NONE ? brand : null)
        go(paths.project(project.id))
      }
      setName(''); setAdding(null); setError('')
    } catch (err) {
      setError(errorText(err))
    }
  }

  const current = list.find((p) => p.id === projectId)
  const brandName = brand === ALL ? '모든 브랜드' : brand === NONE ? '브랜드 없음' : brandList.find((b) => b.id === brand)?.name || '브랜드'
  const shown = list.filter((p) => !query.trim() || (p.name || '').includes(query.trim()))
  const initial = (user.creator_name || '나').slice(0, 1)
  const showMemo = projectId && tab !== 'chat'

  return (
    <div className={`workspace project-workspace-shell${open ? ' sidebar-open' : ''}`}>
      <button type="button" className="mobile-sidebar-backdrop" aria-label="프로젝트 목록 닫기" onClick={() => setOpen(false)} />
      <aside className="project-panel unified-sidebar" id="workspace-sidebar" aria-label="프로젝트 목록">
        <button type="button" className="mobile-sidebar-close" onClick={() => setOpen(false)}>닫기 ×</button>
        <a className="sidebar-wordmark" href={`#${paths.home()}`} style={{ textDecoration: 'none' }}>AX STUDIO</a>

        <details className="doc-menu brand-picker">
          <summary>
            <span className="brand-picker-avatar">{brandName.slice(0, 1)}</span>
            <span className="brand-picker-name"><small>현재 브랜드</small><strong>{brandName}</strong></span>
          </summary>
          <div className="doc-menu-panel brand-picker-panel">
            <span className="brand-picker-caption">브랜드 전환</span>
            <button type="button" onClick={(e) => pickBrand(ALL, e)}><span>모든 브랜드</span></button>
            <button type="button" onClick={(e) => pickBrand(NONE, e)}><span>브랜드 없음</span></button>
            <div className="account-menu-separator" />
            {brandList.map((b) => (
              <button key={b.id} type="button" onClick={(e) => pickBrand(b.id, e)}><span>{b.name || '이름 없는 브랜드'}</span></button>
            ))}
            <button type="button" onClick={(e) => { setAdding('brand'); setName(''); e.currentTarget.closest('details')?.removeAttribute('open') }}>
              <span>＋ 새 브랜드</span>
            </button>
          </div>
        </details>

        <div className="project-section-heading"><h2>프로젝트</h2></div>
        <label className="sidebar-search">
          <span className="sr-only">프로젝트 검색</span>
          <input type="search" placeholder="검색" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <div className="sidebar-project-list">
          {shown.map((p) => (
            <div key={p.id} className="project-entry">
              <button type="button" className={`project-item${p.id === projectId ? ' active' : ''}`} onClick={() => go(paths.project(p.id))}>
                <strong>{p.name}</strong>
              </button>
            </div>
          ))}
          {!shown.length && <p className="muted small" style={{ padding: '0 10px' }}>{query ? '찾는 프로젝트가 없어요.' : '아직 프로젝트가 없어요.'}</p>}
        </div>

        {adding ? (
          <form className="stack" style={{ gap: 8 }} onSubmit={submit}>
            <input autoFocus aria-label={adding === 'brand' ? '새 브랜드 이름' : '새 프로젝트 이름'}
              placeholder={adding === 'brand' ? '브랜드 이름 (예: 온유칠보)' : '프로젝트 이름 (예: 칠보 팔찌)'} value={name} onChange={(e) => setName(e.target.value)} />
            {error && <p className="form-error">{error}</p>}
            <div className="row">
              <button type="button" className="small" onClick={() => { setAdding(null); setError('') }}>취소</button>
              <button className="primary small">만들기</button>
            </div>
          </form>
        ) : (
          <button type="button" className="ghost small" onClick={() => { setAdding('project'); setName('') }}>＋ 새 프로젝트</button>
        )}

        <div className="bottom">
          <details className="doc-menu account-menu">
            <summary className="account-trigger">
              <span className="profile-avatar">{initial}</span>
              <span className="profile-info"><strong>{user.creator_name}</strong><small>{user.email}</small></span>
              <span className="account-more">⋯</span>
            </summary>
            <div className="doc-menu-panel account-menu-panel">
              <button type="button" onClick={onLogout}><span>로그아웃</span></button>
            </div>
          </details>
        </div>
      </aside>

      <main className={projectId ? 'main project-workspace' : 'main'} inert={open ? '' : undefined}>
        {projectId ? (
          <header className="project-work-header workspace-nav-header">
            <div className="workspace-navigation">
              <button type="button" className="mobile-project-toggle work-project-toggle" aria-label="프로젝트 목록 열기"
                aria-controls="workspace-sidebar" aria-expanded={open} onClick={() => setOpen(true)}>☰</button>
              <nav className="project-work-tabs" aria-label="프로젝트 작업 메뉴">
                {TABS.map(([key, label]) => (
                  <a key={key} href={`#${key === 'home' ? paths.project(projectId) : `/p/${projectId}/${key}`}`}
                    aria-current={tab === key ? 'page' : undefined}>{label}</a>
                ))}
              </nav>
              {showMemo && (
                <div className="workspace-header-actions">
                  <button type="button" className="work-panel-toggle" aria-controls="work-context-panel" aria-expanded={memoOpen}
                    onClick={() => setMemoOpen(!memoOpen)}>
                    <span className="workspace-memo-label">메모·기록</span><span aria-hidden="true">☷</span>
                  </button>
                </div>
              )}
            </div>
          </header>
        ) : (
          <button type="button" className="mobile-project-toggle" aria-label="프로젝트 목록 열기" onClick={() => setOpen(true)}>
            ☰ <span className="mobile-project-name">{current?.name || 'AX STUDIO'}</span>
          </button>
        )}
        <div className={`work-body${showMemo && memoOpen ? ' work-panel-open' : ''}`}>
          <div id="content">{children}</div>
          {showMemo && <MemoPanel projectId={projectId} open={memoOpen} refreshKey={tab} onClose={() => setMemoOpen(false)} />}
        </div>
      </main>
    </div>
  )
}
