// 사진·파일 첨부 공용 조각. 서버 규칙: PNG·JPEG·WebP 사진, TXT·MD·DOCX 문서, 파일당 2MB.
import { API_BASE, assets } from '../api/index.js'

export const PHOTO_ACCEPT = '.png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp'
export const FILE_ACCEPT = '.txt,.md,.docx,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document'
export const MAX_FILE_BYTES = 2 * 1024 * 1024

export const fileHref = (assetId) => API_BASE + assets.fileUrl(assetId)
export const isImage = (asset) => asset.mime_type?.startsWith('image/')

// 고른 파일들을 올리고 [{asset}|{error}]를 돌려준다. 크기는 먼저 걸러서 불필요한 업로드를 막는다.
export async function uploadFiles(projectId, fileList, kind) {
  const results = []
  for (const file of fileList) {
    if (file.size > MAX_FILE_BYTES) {
      results.push({ error: `${file.name}: 파일은 2MB까지 올릴 수 있어요.` })
      continue
    }
    try {
      results.push({ asset: await assets.upload(projectId, file, kind === 'photo' ? 'product_image' : 'document') })
    } catch (err) {
      results.push({ error: `${file.name}: ${err.message || '올리지 못했어요.'}` })
    }
  }
  return results
}

const FileIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" />
  </svg>
)

// 메시지에 붙은 파일 목록. onRemove가 있으면 보내기 전 첨부(지울 수 있음)다.
export function FileChips({ items, onRemove }) {
  if (!items.length) return null
  return (
    <div className={`chat-attachments${onRemove ? ' chat-draft-attachments' : ''}`}>
      {items.map((item) => (
        <div key={item.id} className="chat-file-chip">
          <a href={fileHref(item.id)} target="_blank" rel="noopener noreferrer" title={item.file_name} style={{ display: 'inline-flex', gap: 6, alignItems: 'center', color: 'inherit', textDecoration: 'none' }}>
            <FileIcon /><span>{item.file_name}</span>
          </a>
          {onRemove && <button type="button" aria-label={`${item.file_name} 첨부 취소`} onClick={() => onRemove(item.id)}>×</button>}
        </div>
      ))}
    </div>
  )
}
