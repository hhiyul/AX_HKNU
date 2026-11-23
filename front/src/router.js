// 해시 라우터. 서버 설정 없이 nginx 정적 호스팅에서도 새로고침이 된다.
import { useEffect, useState } from 'react'

function parse() {
  const raw = window.location.hash.replace(/^#/, '') || '/home'
  const [path, search = ''] = raw.split('?')
  return { path, parts: path.split('/').filter(Boolean), query: Object.fromEntries(new URLSearchParams(search)) }
}

export function useRoute() {
  const [route, setRoute] = useState(parse)
  useEffect(() => {
    const onChange = () => setRoute(parse())
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
