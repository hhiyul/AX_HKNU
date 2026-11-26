import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/prototype.css'
import './styles/app.css'
import App from './App.jsx'
import ThemeToggle from './components/ThemeToggle.jsx'
import { applyInitialTheme } from './theme.js'

// 첫 그림 전에 테마를 정해 밝은 화면이 번쩍이지 않게 한다.
applyInitialTheme()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
    <ThemeToggle />
  </StrictMode>,
)
