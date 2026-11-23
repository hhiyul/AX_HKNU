// 로그인·회원가입(프로토타입 auth 화면 틀). PC에서는 왼쪽에 소개, 폰에서는 입력만 보인다.
import { useState } from 'react'
import { auth, errorText } from '../api/index.js'

export default function Login({ onDone }) {
  const [mode, setMode] = useState('login')
  const [form, setForm] = useState({ email: '', password: '', name: '' })
  const [show, setShow] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value })
  const signup = mode === 'signup'

  const submit = async (event) => {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      if (signup) await auth.signup(form.email, form.password, form.name || '창작자')
      else await auth.login(form.email, form.password)
      onDone()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-shell">
      <aside className="auth-story">
        <div className="row"><span className="mark">AX</span><strong>AX STUDIO</strong></div>
        <div>
          <div className="eyebrow">YOUR CREATIVE WORKSPACE</div>
          <h1>작품은 당신이,<br />상품화의 과정은 함께.</h1>
          <div className="gap" />
          <p>펀딩 상세페이지와 사업계획서를 항목별로 쓰고,{'\n'}해야 할 일까지 이어 갑니다.</p>
        </div>
        <small>창작자를 위한 상품화 작업실</small>
      </aside>
      <main className="auth-content">
        <div className="auth-box">
          <h1>{signup ? '회원가입' : '이메일로 로그인'}</h1>
          <p className="muted">{signup ? '이메일과 비밀번호로 바로 시작해요.' : '이전에 정리하던 작업을 이어가세요.'}</p>
          <form onSubmit={submit}>
            <div className="form-field">
              <label htmlFor="auth-email">이메일</label>
              <input id="auth-email" type="email" autoComplete="email" required placeholder="name@example.com" value={form.email} onChange={set('email')} />
            </div>
            <div className="form-field">
              <label htmlFor="auth-password">비밀번호</label>
              <div className="password-input">
                <input id="auth-password" type={show ? 'text' : 'password'} required minLength={8} placeholder="8자 이상 입력"
                  autoComplete={signup ? 'new-password' : 'current-password'} value={form.password} onChange={set('password')} />
                <button type="button" onClick={() => setShow(!show)}>{show ? '숨김' : '표시'}</button>
              </div>
            </div>
            {signup && (
              <div className="form-field">
                <label htmlFor="auth-name">활동 이름</label>
                <input id="auth-name" placeholder="창작자" value={form.name} onChange={set('name')} />
              </div>
            )}
            <p className="form-error" role="alert">{error}</p>
            <button className="primary wide" disabled={busy}>{signup ? '가입하고 시작하기' : '로그인'}</button>
          </form>
          <div className="auth-links">
            <button type="button" className="ghost small" onClick={() => { setMode(signup ? 'login' : 'signup'); setError('') }}>
              {signup ? '이미 계정이 있어요' : '회원가입'}
            </button>
          </div>
        </div>
      </main>
    </div>
  )
}
