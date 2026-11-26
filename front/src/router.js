// 해시 라우터. 서버 설정 없이 nginx 정적 호스팅에서도 새로고침이 된다.
import { useEffect, useState } from 'react'

function parse() {
  const raw = window.location.hash.replace(/^#/, '') || '/home'
  const [path, search = ''] = raw.split('?')
  return { path, parts: path.split('/').filter(Boolean), query: Object.fromEntries(new URLSearchParams(search)) }
}

// 화면을 떠나기 전에 확인할 일(저장 안 한 글 등). 링크·go()·뒤로 가기 모두 hashchange로 들어오므로 여기서 한 번에 막는다.
// guard는 Promise<boolean>을 돌려준다. false면 주소를 원래대로 두고 화면도 그대로 둔다.
let leaveGuard = null
let guardRunning = false
let shownHash = window.location.hash

export function setLeaveGuard(guard) {
  leaveGuard = guard
  return () => { if (leaveGuard === guard) leaveGuard = null }
}

async function passGuard(target) {
  // 주소만 먼저 바뀌었으니 확인이 끝날 때까지 원래 주소로 되돌려 둔다(replaceState는 hashchange를 다시 일으키지 않는다).
  window.history.replaceState(null, '', shownHash || '#/home')
  if (guardRunning) return false
  guardRunning = true
  try {
    const allowed = await leaveGuard().catch(() => false)
    if (allowed) window.history.replaceState(null, '', target)
    return allowed
  } finally {
    guardRunning = false
  }
}

export function useRoute() {
  const [route, setRoute] = useState(parse)
  useEffect(() => {
    const onChange = async () => {
      const target = window.location.hash
      if (target === shownHash) return
      if (leaveGuard && !(await passGuard(target))) return
      shownHash = target
      setRoute(parse())
    }
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export function go(path, query) {
  const search = query ? `?${new URLSearchParams(query)}` : ''
  window.location.hash = `#${path}${search}`
}

export const paths = {
  home: () => '/home',
  login: () => '/login',
  project: (id) => `/p/${id}`,
  docs: (id) => `/p/${id}/docs`,
  doc: (id, docId) => `/p/${id}/docs/${docId}`,
  tasks: (id) => `/p/${id}/tasks`,
  plan: (id) => `/p/${id}/plan`,
  chat: (id) => `/p/${id}/chat`,
  settlement: (id) => `/p/${id}/settlement`,
}
