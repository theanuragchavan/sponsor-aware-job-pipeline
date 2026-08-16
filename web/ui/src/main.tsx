import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// Registered only in a production build. In dev the service worker would serve
// a cached shell over Vite's HMR and quietly hide every change you just made,
// which costs an afternoon the first time it happens.
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // A failed registration means no offline shell. The app works without
      // it, so this is not worth interrupting anyone over.
    })
  })
}
