import { useQuery } from '@tanstack/react-query'
import { Activity, Compass, LayoutDashboard, Settings, Trophy, Upload } from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../api/client'
import { FB, FC } from './ui'

const links = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/activities', label: 'Allenamenti', icon: Activity },
  { to: '/routes', label: 'Percorsi', icon: Compass },
  { to: '/records', label: 'Record', icon: Trophy },
  { to: '/sync', label: 'Import', icon: Upload },
  { to: '/settings', label: 'Impostazioni', icon: Settings },
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

const cls = ({ isActive }: { isActive: boolean }) => (isActive ? 'text-[#eef1f4]' : 'text-[#aab2bd] hover:text-[#eef1f4]')
const tab = (s: { isActive: boolean }) => `rounded-lg px-3.5 py-2 ${s.isActive ? 'bg-[#20252c] text-[#eef1f4]' : 'text-[#aab2bd] hover:text-[#eef1f4]'}`

export default function Shell() {
  return (
    <div className="flex min-h-screen flex-col overflow-x-hidden" style={{ fontFamily: FB }}>
      <header className="sticky top-0 z-10 border-b border-[#262b33] bg-[#111418]">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-6 px-4 md:px-8">
          <NavLink to="/" className="flex items-center gap-2.5 text-[#eef1f4]">
            <img src="/favicon.svg" width={28} height={28} alt="" />
            <span className="text-[22px] font-bold tracking-[.02em] uppercase" style={{ fontFamily: FC }}>Cadence</span>
          </NavLink>
          <nav className="ml-auto hidden gap-1 text-[15px] font-medium md:flex">
            {links.map((l) => (
              <NavLink key={l.to} to={l.to} end={l.to === '/'} className={tab}>
                {l.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto md:ml-0">
            <Connection />
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 pt-7 pb-20 md:px-8 md:pb-14">
        <Outlet />
      </main>
      <nav className="fixed inset-x-0 bottom-0 z-10 grid grid-cols-6 border-t border-[#262b33] bg-[#111418] md:hidden">
        {links.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} end={to === '/'} className={(s) => `flex min-h-14 min-w-0 flex-col items-center justify-center gap-0.5 text-[11px] ${cls(s)}`}>
            <Icon size={20} aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
