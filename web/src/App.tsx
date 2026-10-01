import { QueryClientProvider } from '@tanstack/react-query'
import { lazy, Suspense } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { queryClient } from './api/client'
import Shell from './components/Shell'

import ActivitiesPage from './pages/ActivitiesPage'
import RecordsPage from './pages/RecordsPage'
import SettingsPage from './pages/SettingsPage'
import SyncPage from './pages/SyncPage'

// MapLibre + ECharts only load on the detail route
const ActivityDetailPage = lazy(() => import('./pages/ActivityDetailPage'))
const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const loading = <p className="text-sm text-neutral-500">Loading…</p>

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<Suspense fallback={loading}><DashboardPage /></Suspense>} />
            <Route path="activities" element={<ActivitiesPage />} />
            <Route
              path="activities/:id"
              element={
                <Suspense fallback={loading}>
                  <ActivityDetailPage />
                </Suspense>
              }
            />
            <Route path="records" element={<RecordsPage />} />
            <Route path="sync" element={<SyncPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
