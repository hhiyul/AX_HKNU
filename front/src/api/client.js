// 백엔드 호출의 공통 처리. 화면 코드는 fetch를 직접 쓰지 않고 이 파일을 통해 부른다.
// 다른 화면(조원 프론트)으로 바꿀 때 src/api 폴더를 그대로 가져가면 된다.

export const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(status, code, message, data) {
    super(message || code || `HTTP ${status}`)
    this.status = status
    this.code = code
    this.data = data
  }
}

// 사용자에게 보여 줄 오류 문구. 백엔드 message가 있으면 그대로 쓴다.
export function errorText(error) {
  if (error instanceof ApiError) return error.message
  return '연결에 실패했습니다. 잠시 뒤 다시 시도해 주세요.'
}

export async function request(method, path, { body, query, idempotencyKey, raw } = {}) {
  const url = new URL(API_BASE + path, window.location.origin)
  for (const [key, value] of Object.entries(query || {})) {
    if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value)
  }
  const headers = {}
  let payload
  if (body instanceof FormData) {
    payload = body
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey
  const response = await fetch(url, { method, headers, body: payload, credentials: 'include' })
  if (raw) {
    if (!response.ok) throw new ApiError(response.status, 'HTTP_ERROR', '파일을 받지 못했습니다.')
    return response
  }
  const text = await response.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!response.ok) {
    const detail = data && typeof data === 'object' ? data : {}
    throw new ApiError(response.status, detail.code, detail.message, detail)
  }
  return data
}

export const api = {
  get: (path, options) => request('GET', path, options),
  post: (path, body, options) => request('POST', path, { ...options, body }),
  put: (path, body, options) => request('PUT', path, { ...options, body }),
  patch: (path, body, options) => request('PATCH', path, { ...options, body }),
  del: (path, options) => request('DELETE', path, options),
}

export function newKey() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}
