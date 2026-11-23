// UT용 임시 화면. 로그인 상태를 확인하고 해시 경로에 맞는 화면을 프로토타입 작업실 틀 안에 그린다.
import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { auth } from './api/index.js'
import Shell from './components/Shell.jsx'
import { go, paths, useRoute } from './router.js'
import Home from './screens/Home.jsx'
import Login from './screens/Login.jsx'
import Project, { tabOf } from './screens/Project.jsx'

// 예전 API 테스트 화면은 #/dev에서만 따로 불러온다(UT 화면과 CSS가 섞이지 않게).
const DevConsole = lazy(() => import('./dev/DevConsole.jsx'))

export default function App() {
  const route = useRoute()
  const [user, setUser] = useState(undefined)

  const refresh = useCallback(() => auth.me().then(setUser).catch(() => setUser(null)), [])
  useEffect(() => { refresh() }, [refresh])

  if (route.parts[0] === 'dev') return <Suspense fallback={null}><DevConsole /></Suspense>
  if (user === undefined) return <p className="muted" style={{ padding: 24 }}>불러오는 중…</p>
  if (!user || route.parts[0] === 'login') {
    return <Login onDone={() => refresh().then(() => go(paths.home()))} />
  }
  const logout = () => auth.logout().finally(() => { setUser(null); go(paths.login()) })
  const projectId = route.parts[0] === 'p' ? route.parts[1] : null

  return (
    <Shell user={user} projectId={projectId} tab={projectId ? tabOf(route) : null} onLogout={logout}>
      {projectId ? <Project projectId={projectId} route={route} /> : <Home />}
    </Shell>
  )
}
