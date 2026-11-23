// AI 답변의 마크다운(굵게·목록·표)을 그린다. HTML은 그리지 않는다(react-markdown 기본값).
// remark-cjk-friendly: **“따옴표”**이며 처럼 한글 바로 앞에서 닫히는 굵게도 인식한다.
import ReactMarkdown from 'react-markdown'
import remarkCjkFriendly from 'remark-cjk-friendly'
import remarkGfm from 'remark-gfm'

const PLUGINS = [remarkGfm, remarkCjkFriendly]
const COMPONENTS = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
}

export default function Markdown({ children }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={PLUGINS} components={COMPONENTS}>{children || ''}</ReactMarkdown>
    </div>
  )
}
