// 화면 오른쪽 위의 다크모드 전환 버튼.
import { useState } from 'react'
import { currentTheme, setTheme } from '../theme.js'

export default function ThemeToggle() {
  const [theme, setThemeState] = useState(currentTheme)
  const isDark = theme === 'dark'

  const toggle = () => {
    const next = isDark ? 'light' : 'dark'
    setTheme(next)
    setThemeState(next)
  }

  return (
    <button type="button" className="theme-toggle" onClick={toggle}
      aria-label={isDark ? '밝은 화면으로 바꾸기' : '어두운 화면으로 바꾸기'} title={isDark ? '밝은 화면' : '어두운 화면'}>
      <span aria-hidden="true">{isDark ? '☀' : '☾'}</span>
    </button>
  )
}
