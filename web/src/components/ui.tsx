import type { ReactNode } from 'react'

// Design tokens from the "Diario di Corsa" mockup (same values as DashboardPage)
export const FC = "'Barlow Condensed', sans-serif"
export const FM = "'IBM Plex Mono', monospace"
export const FB = "'Barlow', Inter, system-ui, sans-serif"
export const BLUE = '#4c8dff'
export const MUTED = '#8a93a0'
export const SOFT = '#aab2bd'
export const SURF = '#191d23'

export const surface = 'min-w-0 rounded-[14px] bg-[#191d23] p-5'
export const field = 'rounded-lg border border-[#262b33] bg-[#111418] px-2.5 py-1 text-sm text-[#eef1f4]'
export const btn = 'inline-flex min-h-10 items-center rounded-full bg-[#eef1f4] px-4 text-sm font-semibold text-[#111418] disabled:opacity-50 md:min-h-9'
export const btnGhost = 'inline-flex min-h-10 items-center rounded-full border border-[#262b33] px-4 text-sm font-semibold text-[#aab2bd] hover:text-[#eef1f4] disabled:opacity-50 md:min-h-9'
export const pill = (on: boolean) => `min-h-10 rounded-full border px-4 text-sm font-semibold md:min-h-9 ${on ? 'border-[#eef1f4] bg-[#eef1f4] text-[#111418]' : 'border-[#262b33] text-[#aab2bd] hover:text-[#eef1f4]'}`
export const eyebrow = 'text-xs tracking-[.08em] uppercase text-[#8a93a0]'

export const Fonts = () => (
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Barlow:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" />
)

export function PageHead({ eyebrow: e, title, children }: { eyebrow?: ReactNode; title: ReactNode; children?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {e && <div className={eyebrow} style={{ fontFamily: FM }}>{e}</div>}
        <h1 className="mt-1 text-[44px] leading-none font-bold tracking-[.01em] text-[#eef1f4] uppercase" style={{ fontFamily: FC }}>{title}</h1>
      </div>
      {children}
    </header>
  )
}

export function Panel({ title, sub, aside, className = '', children }: { title?: string; sub?: ReactNode; aside?: ReactNode; className?: string; children: ReactNode }) {
  return (
    <section className={`${surface} ${className}`}>
      {(title || aside) && (
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div>
            {title && <h2 className="m-0 text-[22px] font-semibold tracking-[.02em] text-[#eef1f4] uppercase" style={{ fontFamily: FC }}>{title}</h2>}
            {sub && <p className="mt-0.5 text-[13px]" style={{ color: MUTED }}>{sub}</p>}
          </div>
          {aside}
        </div>
      )}
      {children}
    </section>
  )
}
