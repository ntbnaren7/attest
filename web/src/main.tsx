import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { Guard } from './components/Guard'
import { PrefsProvider } from './prefs'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PrefsProvider>
      <Guard label="app">
        <App />
      </Guard>
    </PrefsProvider>
  </StrictMode>,
)
