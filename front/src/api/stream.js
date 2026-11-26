// AI 실행 진행 상황(SSE). 연결이 잠깐 끊겨도 실행이 아직 돌고 있으면 실패로 끝내지 않고 다시 이어 받는다.
import { API_BASE } from './client.js'
import { runs } from './index.js'

const TERMINAL = new Set(['completed', 'failed', 'stopped', 'outcome_unknown'])
// 상태 API의 값 → 끝 사건 이름. 화면은 끝 사건 이름으로만 판단한다.
const STATUS_KIND = { completed: 'completed', failed: 'failed', stopped: 'stopped', unknown: 'outcome_unknown' }
// 브라우저가 다시 연결을 포기했을 때 우리가 다시 여는 간격.
const REOPEN_MS = 3000
// 서버에 아예 닿지 않는 상태가 이만큼 이어지면 포기한다.
const GIVE_UP_MS = 2 * 60 * 1000
const LOST_MESSAGE = '연결이 끊겨 진행 상황을 받지 못했어요. 새로고침하면 답변을 다시 확인할 수 있어요.'

// onEvent({seq, kind, content})를 부르고, 끝나면 onDone(kind, content)을 한 번 부른다. 반환값으로 닫는다.
export function followRun(projectId, runId, { onEvent, onDone }) {
  let source = null
  let lastSeq = 0
  let finished = false
  let checking = false
  let reopenTimer = null
  let unreachableSince = null

  const close = () => {
    finished = true
    clearTimeout(reopenTimer)
    source?.close()
  }
  const finish = (kind, content) => {
    if (finished) return
    close()
    onDone?.(kind, content)
  }

  const open = () => {
    const after = lastSeq ? `?after=${lastSeq}` : ''
    source = new EventSource(`${API_BASE}${runs.streamUrl(projectId, runId)}${after}`, { withCredentials: true })
    source.onmessage = (message) => {
      let event
      try {
        event = JSON.parse(message.data)
      } catch {
        return
      }
      unreachableSince = null
      if (typeof event.seq === 'number') lastSeq = Math.max(lastSeq, event.seq)
      onEvent?.(event)
      if (TERMINAL.has(event.kind)) finish(event.kind, event.content)
    }
    source.onerror = () => { if (!finished) check() }
  }

  // 스트림이 끊기면(서버가 닫았거나 네트워크 문제) 실행 상태를 보고 정한다.
  // 끝났으면 마무리, 아직 돌고 있으면 다시 연결(브라우저가 포기했으면 직접 다시 연다).
  const check = async () => {
    if (checking || finished) return
    checking = true
    unreachableSince ??= Date.now()
    try {
      const run = await runs.get(projectId, runId)
      unreachableSince = null
      if (run.status !== 'running') {
        finish(STATUS_KIND[run.status] || 'failed', '')
        return
      }
    } catch (err) {
      // 실행이 없거나 권한이 없으면 기다려도 소용없다.
      if (err?.status === 404 || err?.status === 403) { finish('failed', LOST_MESSAGE); return }
      if (Date.now() - unreachableSince > GIVE_UP_MS) { finish('failed', LOST_MESSAGE); return }
    } finally {
      checking = false
    }
    if (!finished && source.readyState === EventSource.CLOSED) {
      clearTimeout(reopenTimer)
      reopenTimer = setTimeout(() => { if (!finished) open() }, REOPEN_MS)
    }
  }

  open()
  return close
}
