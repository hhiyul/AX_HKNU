// 프로젝트 안의 화면 고르기: 작업실 · AI협업 · 일정 · 정산 · 저장함(문서 편집 포함) · 상품기획.
import Chat from './Chat.jsx'
import DocEditor from './DocEditor.jsx'
import Docs from './Docs.jsx'
import Plan from './Plan.jsx'
import Settlement from './Settlement.jsx'
import Tasks from './Tasks.jsx'
import Workroom from './Workroom.jsx'

// 상품기획은 작업실의 "기본 기획" 칸에서 들어간다(프로토타입과 같음). 탭 표시는 작업실로 둔다.
export function tabOf(route) {
  const page = route.parts[2] || 'home'
  return page === 'plan' ? 'home' : page
}

export default function Project({ projectId, route }) {
  const page = route.parts[2] || 'home'
  const docId = page === 'docs' ? route.parts[3] : null
  // 프로젝트가 바뀌면 채팅 화면을 새로 만든다(이전 프로젝트의 대화로 새 프로젝트를 부르지 않게).
  if (page === 'chat') return <Chat key={projectId} projectId={projectId} conversationId={route.query.c} />
  if (page === 'tasks') return <Tasks projectId={projectId} />
  if (page === 'plan') return <Plan projectId={projectId} />
  if (page === 'settlement') return <Settlement projectId={projectId} page={route.parts[3]} />
  if (page === 'docs') {
    return docId ? <DocEditor key={docId} projectId={projectId} docId={docId} sectionId={route.query.s} /> : <Docs projectId={projectId} />
  }
  return <Workroom projectId={projectId} />
}
