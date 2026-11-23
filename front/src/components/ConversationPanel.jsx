// 대화 목록(프로토타입 conversation-panel): 검색, 새 대화, 이름 변경, 휴지통·복원.
import { useEffect, useState } from 'react'
import { conversations, errorText } from '../api/index.js'
import { formatDate } from './ui.jsx'

const NEW_TITLE = '새 대화'

export default function ConversationPanel({ projectId, list, currentId, open, onSelect, onNew, onClose, onChanged }) {
  const [query, setQuery] = useState('')
  const [trashMode, setTrashMode] = useState(false)
  const [trash, setTrash] = useState([])
  const [error, setError] = useState('')

  useEffect(() => {
    if (trashMode) conversations.list(projectId, 'trash').then(setTrash).catch((err) => setError(errorText(err)))
  }, [trashMode, projectId, list])

  const closeMenu = (event) => event.currentTarget.closest('details')?.removeAttribute('open')
  const rename = async (item, event) => {
    closeMenu(event)
    const title = window.prompt('대화 이름', item.title || '')
    if (!title || !title.trim() || title.trim() === item.title) return
    try { await conversations.rename(projectId, item.id, title.trim(), item.revision); onChanged() } catch (err) { setError(errorText(err)); onChanged() }
  }
  const remove = async (item, event) => {
    closeMenu(event)
    if (!window.confirm(`"${item.title || NEW_TITLE}"을(를) 휴지통으로 옮길까요? 휴지통에서 다시 꺼낼 수 있어요.`)) return
    try { await conversations.trash(projectId, item.id); onChanged(item.id) } catch (err) { setError(errorText(err)) }
  }
  const restore = async (item) => {
    try { await conversations.restore(projectId, item.id); setTrashMode(false); onChanged(); onSelect(item.id) } catch (err) { setError(errorText(err)) }
  }

  const words = query.trim()
  const shown = (trashMode ? trash : list).filter((c) => !words || `${c.title || ''} ${c.preview}`.includes(words))

  return (
    <aside className="conversation-panel" aria-label="대화 목록" hidden={!open}>
      <div className="conversation-panel-head">
        <h3>{trashMode ? '휴지통의 대화' : '대화'}</h3>
        <div className="conversation-panel-actions row" style={{ gap: 4 }}>
          {!trashMode && <button type="button" className="conversation-compose" aria-label="새 대화" title="새 대화" onClick={onNew}>＋</button>}
          <button type="button" className="conversation-close" aria-label="대화 목록 닫기" onClick={onClose}>×</button>
        </div>
      </div>
      <label className="conversation-search">
        <span className="sr-only">대화 검색</span>
        <input type="search" placeholder="대화 검색" value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>
      {error && <div className="notice error">{error}</div>}
      <div className="conversation-results" style={{ overflowY: 'auto', minHeight: 0, flex: 1 }}>
        {!shown.length && <p className="conversation-empty">{words ? '찾는 대화가 없어요.' : trashMode ? '휴지통이 비어 있어요.' : '아직 대화가 없어요. 아래에서 질문을 보내면 대화가 시작돼요.'}</p>}
        {shown.map((item) => (
          <div key={item.id} className="conversation-entry" style={{ position: 'relative' }}>
            <button type="button" className={`conversation-select${item.id === currentId && !trashMode ? ' selected' : ''}`}
              aria-current={item.id === currentId && !trashMode ? 'true' : undefined}
              onClick={() => (trashMode ? restore(item) : onSelect(item.id))}>
              <strong>{item.title || NEW_TITLE}</strong>
              <span>{trashMode ? '눌러서 복원' : item.preview || '아직 메시지가 없어요'}</span>
              <small>{formatDate(item.last_message_at || item.created_at)}</small>
            </button>
            {!trashMode && (
              <details className="conversation-menu">
                <summary aria-label={`${item.title || NEW_TITLE} 메뉴`}>⋯</summary>
                <div>
                  <button type="button" onClick={(e) => rename(item, e)}>이름 변경</button>
                  <button type="button" className="danger" onClick={(e) => remove(item, e)}>휴지통으로</button>
                </div>
              </details>
            )}
          </div>
        ))}
      </div>
      <button type="button" className="conversation-trash-toggle small" onClick={() => setTrashMode(!trashMode)}>
        {trashMode ? '← 대화 목록' : '휴지통'}
      </button>
    </aside>
  )
}
