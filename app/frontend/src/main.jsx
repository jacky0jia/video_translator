import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import ErrorBoundary from './components/ErrorBoundary'
import { I18nProvider } from './contexts/I18nContext'
import { ToastProvider } from './contexts/ToastContext'
import { ThemeProvider } from './contexts/ThemeContext'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ErrorBoundary>
      <I18nProvider>
        <ThemeProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </ThemeProvider>
      </I18nProvider>
    </ErrorBoundary>
  </StrictMode>,
)
