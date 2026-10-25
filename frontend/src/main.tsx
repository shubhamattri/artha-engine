import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider } from './auth/AuthContext'
import App from './App'
import { ConfirmProvider } from './components/ui'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, staleTime: 30_000 },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfirmProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </ConfirmProvider>
    </QueryClientProvider>
  </React.StrictMode>,
)
// tell the boot watchdog (index.html) the app mounted — stops the "reload" fallback from showing
;(window as unknown as { __artha_mounted?: boolean }).__artha_mounted = true

// No service worker. It caused repeated stale-build / blank-screen issues on this always-online
// app. Proactively remove any worker + cache a device might still have, so everyone lands on plain,
// always-fresh network loads. (The /sw.js kill-switch handles devices that don't run this code.)
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.getRegistrations().then((rs) => rs.forEach((r) => r.unregister())).catch(() => {})
}
if (window.caches) {
  caches.keys().then((ks) => ks.forEach((k) => caches.delete(k))).catch(() => {})
}
