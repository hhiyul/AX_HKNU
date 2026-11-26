import { useEffect, useRef } from 'react'
import './agent.css'

export default function AgentDialog({ title, onClose, children }) {
  const dialog = useRef(null)
  useEffect(() => {
    const element = dialog.current
    const opener = document.activeElement
    element.showModal()
    return () => { element.close(); opener?.focus() }
  }, [])
  return <dialog ref={dialog} className="agent-dialog" aria-label={title} onCancel={(event) => { event.preventDefault(); onClose() }}>
    <div className="agent-dialog-heading"><h2>{title}</h2><button type="button" onClick={onClose} aria-label="닫기">닫기</button></div>
    {children}
  </dialog>
}
