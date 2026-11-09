// 백엔드 동작 확인용 임시 화면. 실제 프론트를 만들 때 통째로 교체한다.
import { useState } from 'react'
import './App.css'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8080/api').replace(/\/$/, '')

// {brand_id} 같은 자리는 아래 "현재 ID" 칸의 값으로 바뀐다.
const PRESETS = [
  ['인증', [
    ['회원가입', 'POST', '/auth/signup', { email: 'test@example.com', password: 'password123', creator_name: '테스터' }],
    ['로그인', 'POST', '/auth/login', { email: 'test@example.com', password: 'password123' }],
    ['내 정보', 'GET', '/auth/me'],
    ['로그아웃', 'POST', '/auth/logout'],
    ['이름 변경', 'PATCH', '/profile', { creator_name: '새 이름' }],
  ]],
  ['브랜드', [
    ['목록', 'GET', '/brands'],
    ['생성', 'POST', '/brands', { brand_type: 'new', name: '테스트 브랜드' }],
    ['조회', 'GET', '/brands/{brand_id}'],
    ['수정', 'PATCH', '/brands/{brand_id}', { description: '소개', keywords: ['미니멀'], colors: ['#225740'] }],
    ['로고 지정', 'PATCH', '/brands/{brand_id}', { logo_asset_id: '{asset_id}' }],
    ['완료', 'PATCH', '/brands/{brand_id}', { status: 'completed' }],
    ['삭제', 'DELETE', '/brands/{brand_id}'],
  ]],
  ['상품', [
    ['목록', 'GET', '/projects'],
    ['브랜드별 목록', 'GET', '/projects?brand_id={brand_id}'],
    ['생성', 'POST', '/projects', { brand_id: '{brand_id}', name: '테스트 상품' }],
    ['조회', 'GET', '/projects/{project_id}'],
    ['수정', 'PATCH', '/projects/{project_id}', { features: ['경량'], expected_price: 15000, current_step: 'price' }],
    ['완료', 'PATCH', '/projects/{project_id}', { status: 'completed' }],
    ['삭제', 'DELETE', '/projects/{project_id}'],
  ]],
  ['파일·작업·OpenAI', [
    ['파일 목록', 'GET', '/assets?brand_id={brand_id}'],
    ['파일 정보', 'GET', '/assets/{asset_id}'],
    ['파일 삭제', 'DELETE', '/assets/{asset_id}'],
    ['작업 목록', 'GET', '/jobs'],
    ['키 연결 테스트', 'POST', '/openai/test', { api_key: 'sk-여기에-키-입력' }],
  ]],
]

const ID_FIELDS = ['brand_id', 'project_id', 'asset_id']

function idFieldFor(path) {
  if (path.startsWith('/brands')) return 'brand_id'
  if (path.startsWith('/projects')) return 'project_id'
  if (path.startsWith('/assets')) return 'asset_id'
  return null
}

export default function App() {
  const [ids, setIds] = useState({ brand_id: '', project_id: '', asset_id: '' })
  const [method, setMethod] = useState('GET')
  const [path, setPath] = useState('/auth/me')
  const [body, setBody] = useState('')
  const [log, setLog] = useState([])
  const [upload, setUpload] = useState({ file: null, asset_type: 'logo', with_project: false })

  const fill = (text) => text.replace(/\{(\w+)\}/g, (match, key) => ids[key] || match)

  const choose = ([, presetMethod, presetPath, presetBody]) => {
    setMethod(presetMethod)
    setPath(presetPath)
    setBody(presetBody ? JSON.stringify(presetBody, null, 2) : '')
  }

  const record = async (label, response) => {
    const text = await response.text()
    let data = text
    try {
      data = text ? JSON.parse(text) : ''
    } catch {
      // JSON이 아니면 글자 그대로 보여준다.
    }
    // 새로 만든 자료의 id를 기억해 다음 요청에 쓴다.
    const field = idFieldFor(label.split(' ')[1] || '')
    if (field && data && typeof data === 'object' && !Array.isArray(data) && data.id) {
      setIds((prev) => ({ ...prev, [field]: data.id }))
    }
    setLog((prev) => [{ label, status: response.status, data, at: new Date().toLocaleTimeString() }, ...prev])
  }

  const fail = (label, error) => {
    setLog((prev) => [{ label, status: '연결 실패', data: String(error), at: new Date().toLocaleTimeString() }, ...prev])
  }

  const send = async () => {
    const finalPath = fill(path)
    const label = `${method} ${finalPath}`
    let payload
    if (body.trim()) {
      try {
        payload = JSON.stringify(JSON.parse(fill(body)))
      } catch {
        fail(label, '본문이 올바른 JSON이 아닙니다.')
        return
      }
    }
    try {
      const response = await fetch(API_BASE + finalPath, {
        method,
        credentials: 'include',
        headers: payload ? { 'Content-Type': 'application/json' } : {},
        body: payload,
      })
      await record(label, response)
    } catch (error) {
      fail(label, error)
    }
  }

  const sendUpload = async () => {
    const label = 'POST /assets (업로드)'
    if (!upload.file) {
      fail(label, '파일을 선택하세요.')
      return
    }
    const form = new FormData()
    form.append('file', upload.file)
    form.append('brand_id', ids.brand_id)
    form.append('asset_type', upload.asset_type)
    if (upload.with_project && ids.project_id) form.append('project_id', ids.project_id)
    try {
      await record('POST /assets', await fetch(`${API_BASE}/assets`, { method: 'POST', credentials: 'include', body: form }))
    } catch (error) {
      fail(label, error)
    }
  }

  return (
    <main>
      <h1>API 테스트</h1>
      <p>
        서버 주소: <code>{API_BASE}</code> · 동작 확인용 임시 화면
      </p>

      <section>
        <h2>현재 ID</h2>
        <p className="hint">생성·조회 응답의 id가 자동으로 채워집니다. 직접 고쳐도 됩니다.</p>
        {ID_FIELDS.map((field) => (
          <label key={field} className="row">
            <span>{field}</span>
            <input value={ids[field]} onChange={(e) => setIds({ ...ids, [field]: e.target.value.trim() })} />
          </label>
        ))}
      </section>

      <section>
        <h2>요청 고르기</h2>
        {PRESETS.map(([group, items]) => (
          <div key={group} className="group">
            <strong>{group}</strong>
            {items.map((preset) => (
              <button key={preset[0] + preset[2]} type="button" onClick={() => choose(preset)}>
                {preset[0]}
              </button>
            ))}
          </div>
        ))}
      </section>

      <section>
        <h2>요청 보내기</h2>
        <div className="row">
          <select value={method} onChange={(e) => setMethod(e.target.value)}>
            {['GET', 'POST', 'PATCH', 'DELETE'].map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
          <input className="grow" value={path} onChange={(e) => setPath(e.target.value)} />
          <button type="button" onClick={send}>
            보내기
          </button>
        </div>
        <textarea rows={7} value={body} onChange={(e) => setBody(e.target.value)} placeholder="JSON 본문 (없으면 비워두기)" />
        <p className="hint">실제로 보낼 경로: {fill(path)}</p>
      </section>

      <section>
        <h2>파일 업로드</h2>
        <p className="hint">현재 brand_id로 올립니다. TXT, PNG, JPEG, WebP · 2MB까지.</p>
        <div className="row">
          <input type="file" onChange={(e) => setUpload({ ...upload, file: e.target.files[0] || null })} />
          <select value={upload.asset_type} onChange={(e) => setUpload({ ...upload, asset_type: e.target.value })}>
            {['logo', 'banner', 'product_image', 'document'].map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
          <label>
            <input
              type="checkbox"
              checked={upload.with_project}
              onChange={(e) => setUpload({ ...upload, with_project: e.target.checked })}
            />{' '}
            현재 project_id에 연결
          </label>
          <button type="button" onClick={sendUpload}>
            업로드
          </button>
        </div>
        {ids.asset_id && (
          <p>
            <a href={`${API_BASE}/assets/${ids.asset_id}/file`} target="_blank" rel="noreferrer">
              현재 asset_id 파일 내려받기
            </a>
          </p>
        )}
      </section>

      <section>
        <h2>
          응답 기록{' '}
          <button type="button" onClick={() => setLog([])}>
            지우기
          </button>
        </h2>
        {log.map((entry, i) => (
          <div key={log.length - i} className="entry">
            <div>
              <b className={typeof entry.status === 'number' && entry.status < 400 ? 'ok' : 'bad'}>{entry.status}</b>{' '}
              {entry.label} <span className="hint">{entry.at}</span>
            </div>
            <pre>{typeof entry.data === 'string' ? entry.data : JSON.stringify(entry.data, null, 2)}</pre>
          </div>
        ))}
      </section>
    </main>
  )
}
