// 화면 테마(밝음/어두움)를 읽고 적용한다. 고른 값은 브라우저에 저장해 다음 방문에도 유지한다.
const STORAGE_KEY = 'ax-theme'

function readSavedTheme() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'dark' || saved === 'light') return saved
  } catch {
    // 저장소를 못 쓰는 환경이면 시스템 설정을 따른다.
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function currentTheme() {
  return document.documentElement.dataset.theme || 'light'
}

export function setTheme(theme) {
  document.documentElement.dataset.theme = theme
  try { localStorage.setItem(STORAGE_KEY, theme) } catch { /* 저장 실패는 이번 방문에만 적용 */ }
}

export function applyInitialTheme() {
  document.documentElement.dataset.theme = readSavedTheme()
}
