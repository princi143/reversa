export function Section({ id, title, subtitle, right, children, className = '' }) {
  return (
    <section id={id} className={`card p-5 ${className}`}>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
          {subtitle && <p className="mt-0.5 text-sm text-ink-400">{subtitle}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  )
}

const TONES = {
  pass: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  fail: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
  pending: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  neutral: 'border-ink-600 bg-ink-800 text-ink-300',
  info: 'border-brand/40 bg-brand/10 text-[#b9c2ff]',
}

export function Badge({ tone = 'neutral', children }) {
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium ${TONES[tone]}`}>
      {children}
    </span>
  )
}

export const STATUS_TONE = {
  HELD: 'pending', BLOCKED: 'fail', COMMITTED: 'pass', RELEASED: 'neutral', CANCELLED: 'neutral',
  ACTIVE: 'info', USED: 'neutral', REVOKED: 'fail',
}

export function CheckIcon({ status }) {
  if (status === 'pass')
    return <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-emerald-500/20 text-[11px] text-emerald-300">✓</span>
  if (status === 'fail')
    return <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-rose-500/20 text-[11px] text-rose-300">✕</span>
  return <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-amber-500/20 text-[11px] text-amber-300">…</span>
}
