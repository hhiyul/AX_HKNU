// 저장함의 참고 자료(프로토타입 materials-section). 프로젝트 사진과 문서 파일을 올리고 보고 지운다.
import { useCallback, useEffect, useRef, useState } from 'react'
import { assets, errorText } from '../api/index.js'
import { FILE_ACCEPT, fileHref, isImage, PHOTO_ACCEPT, uploadFiles } from './Attachments.jsx'

const extension = (name) => (name.split('.').pop() || 'FILE').toUpperCase()

export default function Materials({ projectId }) {
  const [list, setList] = useState(null)
  const [feedback, setFeedback] = useState(null)
  const [busy, setBusy] = useState(false)
  const photo = useRef(null)
  const file = useRef(null)

  const load = useCallback(() => assets.list(projectId).then(setList).catch((err) => setFeedback({ error: errorText(err) })), [projectId])
  useEffect(() => { load() }, [load])

  const add = async (event, kind) => {
    const chosen = [...event.target.files]
    event.target.value = ''
    if (!chosen.length) return
    setBusy(true); setFeedback(null)
    const results = await uploadFiles(projectId, chosen, kind)
    setBusy(false)
    const failed = results.filter((r) => r.error).map((r) => r.error)
    const done = results.length - failed.length
    setFeedback(failed.length ? { error: failed.join('\n') } : { ok: `${done}개를 올렸어요.` })
    load()
  }
  const remove = async (item) => {
    if (!window.confirm(`${item.file_name}을(를) 지울까요? 지우면 되돌릴 수 없어요.`)) return
    try { await assets.remove(item.id); setFeedback({ ok: '지웠어요.' }); load() } catch (err) { setFeedback({ error: errorText(err) }) }
  }

  return (
    <section className="card materials-section">
      <header className="row between wrap">
        <div>
          <h2>참고 자료</h2>
          <p className="muted small">내 상품의 사진과 견적서·메모 파일을 모아두는 곳이에요.</p>
        </div>
        <div className="row wrap">
          <button type="button" className="small" disabled={busy} onClick={() => photo.current?.click()}>＋ 사진 첨부</button>
          <button type="button" className="small" disabled={busy} onClick={() => file.current?.click()}>＋ 파일 첨부</button>
          <input ref={photo} type="file" hidden accept={PHOTO_ACCEPT} multiple onChange={(e) => add(e, 'photo')} />
          <input ref={file} type="file" hidden accept={FILE_ACCEPT} multiple onChange={(e) => add(e, 'file')} />
        </div>
      </header>
      <div className="material-feedback" aria-live="polite">
        {busy && <p className="muted small">올리는 중…</p>}
        {feedback?.ok && <div className="notice">{feedback.ok}</div>}
        {feedback?.error && <div className="notice error" style={{ whiteSpace: 'pre-line' }}>{feedback.error}</div>}
      </div>
      {list && !list.length && (
        <div className="materials-empty">
          <strong>아직 첨부한 자료가 없어요.</strong>
          <p>사진이나 견적서가 생기면 여기에 보관할 수 있어요.</p>
        </div>
      )}
      {list?.length > 0 && (
        <div className="material-grid">
          {list.map((item) => (
            <article key={item.id} className="material-card">
              <a className="material-open" href={fileHref(item.id)} target="_blank" rel="noopener noreferrer" aria-label={`${item.file_name} 열기`}
                style={{ color: 'inherit', textDecoration: 'none' }}>
                <span className="material-icon" aria-hidden="true">
                  {isImage(item) ? <img className="material-thumb" src={fileHref(item.id)} alt="" loading="lazy" /> : extension(item.file_name)}
                </span>
                <span className="material-info">
                  <strong>{item.file_name}</strong>
                  <small>{isImage(item) ? '프로젝트 사진' : '문서'} · {new Date(item.created_at).toLocaleDateString('ko-KR')}</small>
                </span>
              </a>
              <button type="button" className="material-more" aria-label={`${item.file_name} 지우기`} onClick={() => remove(item)}>×</button>
            </article>
          ))}
        </div>
      )}
      <p className="materials-help">파일당 최대 2MB · 사진은 PNG·JPG·WebP, 문서는 TXT·MD·DOCX를 올릴 수 있어요.</p>
    </section>
  )
}
