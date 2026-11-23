// AI 실행 진행 상황(SSE). EventSource는 끊기면 Last-Event-ID로 이어 받는다.
import { API_BASE } from './client.js'
import { runs } from './index.js'

const TERMINAL = new Set(['completed', 'failed', 'stopped', 'outcome_unknown'])

// onEvent({seq, kind, content})를 부르고, 끝나는 사건이 오면 onDone(kind, content)을 부른다. 반환값으로 닫는다.
export function followRun(projectId, runId, { onEvent, onDone }) {
  const source = new EventSource(API_BASE + runs.streamUrl(projectId, runId), { withCredentials: true })
  let finished = false
  source.onmessage = (message) => {
    const event = JSON.parse(message.data)
    onEvent?.(event)
    if (TERMINAL.has(event.kind)) {
      finished = true
      source.close()
      onDone?.(event.kind, event.content)
    }
  }
  source.onerror = () => {
    // 서버가 스트림을 닫았는데 끝 사건을 못 받았으면(10분 지남 등) 상태 API로 마무리한다.
    if (finished) return
    source.close()
    runs.get(projectId, runId)
      .then((run) => onDone?.(run.status, ''))
      .catch(() => onDone?.('failed', '진행 상황을 받지 못했습니다.'))
  }
  return () => source.close()
}
