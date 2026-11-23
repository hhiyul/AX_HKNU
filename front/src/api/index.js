// 백엔드 API를 도메인별 함수로 묶었다. 경로·본문 형식은 README와 Swagger(/docs)가 원본이다.
import { api, newKey, request } from './client.js'

export { ApiError, errorText, API_BASE } from './client.js'

const P = (projectId) => `/projects/${projectId}`

export const auth = {
  me: () => api.get('/auth/me'),
  login: (email, password) => api.post('/auth/login', { email, password }),
  signup: (email, password, creatorName) => api.post('/auth/signup', { email, password, creator_name: creatorName }),
  logout: () => api.post('/auth/logout'),
}

export const projects = {
  // brandId: 비우면 모든 브랜드, 'none'이면 브랜드 없는 프로젝트만
  list: (brandId) => api.get('/projects', { query: { brand_id: brandId } }),
  get: (id) => api.get(P(id)),
  create: (name, plan, brandId) =>
    api.post('/projects', { name, ...(plan ? { plan } : {}), ...(brandId ? { brand_id: brandId } : {}) }, { idempotencyKey: newKey() }),
  // changes: { revision, note?, brand_id?, name?, stage? }
  update: (id, changes) => api.patch(P(id), changes),
}

export const brands = {
  list: () => api.get('/brands'),
  create: (name) => api.post('/brands', { name }),
}

export const economics = {
  get: (projectId) => api.get(`${P(projectId)}/economics`),
  save: (projectId, cost, revision) => api.put(`${P(projectId)}/economics`, { cost, revision }, { idempotencyKey: newKey() }),
  baseline: (projectId, revision) => api.post(`${P(projectId)}/economics/baseline`, { revision }),
  confirm: (projectId, mode, revision) => api.post(`${P(projectId)}/economics/confirm`, { mode, revision }, { idempotencyKey: newKey() }),
}

export const assets = {
  list: (projectId) => api.get('/assets', { query: { project_id: projectId, limit: 100 } }),
  upload: (projectId, file, assetType) => {
    const form = new FormData()
    form.append('file', file)
    form.append('project_id', projectId)
    form.append('asset_type', assetType)
    return api.post('/assets', form)
  },
  remove: (assetId) => api.del(`/assets/${assetId}`),
  fileUrl: (assetId) => `/assets/${assetId}/file`,
}

export const home = {
  get: (brandId) => api.get('/home', { query: { brand_id: brandId } }),
  tasks: (from, to) => api.get('/tasks', { query: { from, to } }),
}

export const plan = {
  list: (projectId) => api.get(`${P(projectId)}/items/plan`),
  save: (projectId, key, content, revision, amount) =>
    api.put(`${P(projectId)}/items/plan/${key}`, { content, revision, ...(amount !== undefined ? { amount } : {}) }),
}

export const docs = {
  templates: () => api.get('/templates'),
  list: (projectId) => api.get(`${P(projectId)}/docs`),
  get: (projectId, docId) => api.get(`${P(projectId)}/docs/${docId}`),
  create: (projectId, templateId, key) =>
    api.post(`${P(projectId)}/docs`, { template_id: templateId }, { idempotencyKey: key }),
  saveDraft: (projectId, docId, sectionId, text, draftRevision) =>
    api.put(`${P(projectId)}/docs/${docId}/sections/${sectionId}/draft`, { text, draft_revision: draftRevision }),
  discardDraft: (projectId, docId, sectionId, draftRevision) =>
    api.del(`${P(projectId)}/docs/${docId}/sections/${sectionId}/draft`, { query: { draft_revision: draftRevision } }),
  save: (projectId, docId, sectionId, text, revision) =>
    api.put(`${P(projectId)}/docs/${docId}/sections/${sectionId}`, { text, revision }),
  review: (projectId, docId, sectionId, revision) =>
    api.post(`${P(projectId)}/docs/${docId}/sections/${sectionId}/review`, { revision }),
  assist: (projectId, docId, sectionId, action, requestText, references) =>
    api.post(`${P(projectId)}/docs/${docId}/sections/${sectionId}/assist`, { action, request: requestText, references }),
  apply: (projectId, docId, sectionId, messageId, draftRevision, confirm = false) =>
    api.post(`${P(projectId)}/docs/${docId}/sections/${sectionId}/apply`, {
      message_id: messageId, draft_revision: draftRevision, confirm,
    }),
  sectionMessages: (projectId, docId, sectionId) =>
    api.get(`${P(projectId)}/docs/${docId}/sections/${sectionId}/messages`),
  proposeTasks: (projectId, docId, sectionId, requestText) =>
    api.post(`${P(projectId)}/docs/${docId}/sections/${sectionId}/task-proposals`, { request: requestText }),
  preview: (projectId, docId) => api.get(`${P(projectId)}/docs/${docId}/preview`),
  exportUrl: (projectId, docId, fmt) => `${P(projectId)}/docs/${docId}/export/${fmt}`,
}

export const tasks = {
  list: (projectId) => api.get(`${P(projectId)}/tasks`),
  create: (projectId, body) => api.post(`${P(projectId)}/tasks`, body),
  update: (projectId, taskId, changes) => api.patch(`${P(projectId)}/tasks/${taskId}`, changes),
}

export const chat = {
  messages: (projectId, conversationId) =>
    api.get(`${P(projectId)}/messages`, { query: { limit: 100, conversation_id: conversationId } }),
  send: (projectId, text, mode = 'chat', assetIds = [], conversationId) =>
    api.post(`${P(projectId)}/runs`, { text, mode, asset_ids: assetIds, ...(conversationId ? { conversation_id: conversationId } : {}) }),
}

export const conversations = {
  list: (projectId, status = 'active') => api.get(`${P(projectId)}/conversations`, { query: { status } }),
  create: (projectId, title) => api.post(`${P(projectId)}/conversations`, title ? { title } : {}),
  rename: (projectId, id, title, revision) => api.patch(`${P(projectId)}/conversations/${id}`, { title, revision }),
  trash: (projectId, id) => api.del(`${P(projectId)}/conversations/${id}`),
  restore: (projectId, id) => api.post(`${P(projectId)}/conversations/${id}/restore`),
}

export const ledger = {
  list: (projectId, status = 'active') => api.get(`${P(projectId)}/ledger`, { query: { status } }),
  create: (projectId, entry) => api.post(`${P(projectId)}/ledger`, entry),
  update: (projectId, id, changes) => api.patch(`${P(projectId)}/ledger/${id}`, changes),
  trash: (projectId, id) => api.del(`${P(projectId)}/ledger/${id}`),
  restore: (projectId, id) => api.post(`${P(projectId)}/ledger/${id}/restore`),
}

export const runs = {
  get: (projectId, runId) => api.get(`${P(projectId)}/runs/${runId}`),
  stop: (projectId, runId) => api.post(`${P(projectId)}/runs/${runId}/stop`),
  streamUrl: (projectId, runId) => `${P(projectId)}/runs/${runId}/stream`,
}

// 내보내기 파일 내려받기(쿠키 인증이라 fetch로 받아 저장)
export async function download(path, filename) {
  const response = await request('GET', path, { raw: true })
  const blob = await response.blob()
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = filename
  link.click()
  URL.revokeObjectURL(link.href)
}
