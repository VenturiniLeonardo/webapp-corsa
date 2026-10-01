import { useQuery } from '@tanstack/react-query'
import { Activity, BarChart3, LayoutDashboard, RefreshCw, Trophy } from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../api/client'

const links = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/activities', label: 'Activities', icon: Activity },
  { to: '/records', label: 'Records', icon: Trophy },
  { to: '/sync', label: 'Sync', icon: RefreshCw },
  { to: '/settings', label: 'Settings', icon: BarChart3 },
]

function Connection() {
  const { isSuccess, isPending } = useQuery({
    queryKey: ['healthz'],
    queryFn: () => api('/healthz'),
    refetchInterval: 30_000,
  })
  const color = isSuccess ? 'bg-emerald-500' : isPending ? 'bg-neutral-500' : 'bg-red-500'
  const label = isSuccess ? 'Connected' : isPending ? 'Connecting' : 'Offline'
  return (
    <span className="flex items-center gap-2 text-xs text-neutral-400">
      <span className={`h-2 w-2 rounded-full ${color}`} />
      {label}
    </span>
  )
}

const cls = ({ isActive }: { isActive: boolean }) => (isActive ? 'text-accent' : 'text-neutral-400 hover:text-neutral-200')

export default function Shell() {
  return (
    <div className="flex min-h-screen flex-col overflow-x-hidden">
      <header className="sticky top-0 z-10 flex h-12 items-center gap-6 border-b border-border bg-bg px-4">
        <span className="font-mono text-sm font-semibold">corsa</span>
        <nav className="hidden gap-5 text-sm md:flex">
          {links.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.to === '/'} className={cls}>
              {l.label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto">
          <Connection />
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 p-4 pb-20 md:pb-4">
        <Outlet />
      </main>
      <nav className="fixed inset-x-0 bottom-0 z-10 grid grid-cols-4 border-t border-border bg-bg md:hidden">
        {links.slice(0, 4).map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} end={to === '/'} className={(s) => `flex min-h-14 flex-col items-center justify-center gap-0.5 text-xs ${cls(s)}`}>
            <Icon size={20} aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
