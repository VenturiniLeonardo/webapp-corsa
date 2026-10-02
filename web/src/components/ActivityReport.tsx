import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { field, surface } from './ui'

/** Plain-text report of one activity (built by the backend, same text the Telegram bot sends). */
export default function ActivityReport({ id }: { id: number }) {
  const report = useQuery({
    queryKey: ['report', id],
    queryFn: () => fetch(`/api/activities/${id}/report`).then((r) => (r.ok ? r.text() : Promise.reject(new Error(`report → ${r.status}`)))),
  })
  const text = report.data ?? ''
  const [copied, setCopied] = useState(false)
  const copy = () => navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) })
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
    Object.assign(document.createElement('a'), { href: url, download: `activity-${id}-report.txt` }).click()
    URL.revokeObjectURL(url)
  }
  return (
    <section className={`${surface} space-y-2`}>
      <div className="flex items-center gap-2">
        <h2 className="mr-auto text-[22px] font-semibold tracking-[.02em] text-[#eef1f4] uppercase">Report (TXT)</h2>
        <button className={`${field} min-h-10 md:min-h-0`} onClick={copy} disabled={!text}>{copied ? 'Copied' : 'Copy'}</button>
        <button className={`${field} min-h-10 md:min-h-0`} onClick={download} disabled={!text}>Download .txt</button>
      </div>
      <pre className="max-h-96 overflow-auto rounded-lg border border-[#262b33] bg-[#111418] p-2 text-xs font-mono tabular-nums whitespace-pre">{report.isError ? 'Report unavailable' : text || 'Loading…'}</pre>
    </section>
  )
}
