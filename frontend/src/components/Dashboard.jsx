import { inr } from '../api.js'

function Stat({ label, value, hint, tone = 'text-ink-100' }) {
  return (
    <div className="card p-4">
      <div className="label">{label}</div>
      <div className={`num mt-1.5 text-2xl font-semibold tracking-tight ${tone}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-ink-400">{hint}</div>}
    </div>
  )
}

export default function Dashboard({ state }) {
  const d = state?.dashboard
  const trip = state?.trip
  return (
    <section id="dashboard" className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-ink-400">
            {trip
              ? `${trip.dest_name} · ${trip.nights} nights · trip ${trip.trip_id} · departs in 10 days`
              : 'No trip yet. Start in Voice Booking below.'}
          </p>
        </div>
        {state && (
          <span className={`text-xs ${state.reconcile.ok ? 'text-emerald-300' : 'text-rose-300'}`}>
            {state.reconcile.ok ? '● Ledger reconciled' : '● Ledger does not reconcile'}
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Trip budget left" value={d?.trip_budget != null ? inr(d.trip_budget_left) : '—'}
          hint={d?.trip_budget != null ? `of ${inr(d.trip_budget)}` : 'set by your request'}
          tone={d?.trip_budget_left < 0 ? 'text-rose-300' : 'text-ink-100'} />
        <Stat label="Wallet available" value={inr(d?.wallet_available)} hint="spendable now" tone="text-emerald-300" />
        <Stat label="Held" value={inr(d?.held)} hint="earmarked, not charged" tone="text-amber-300" />
        <Stat label="Committed" value={inr(d?.committed)} hint={`${d?.bookings ?? 0} confirmed booking(s)`} />
        <Stat label="Refund pending" value={inr(d?.refund_pending)} hint="not available until settled" tone="text-sky-300" />
        <Stat label="Fees paid" value={inr(d?.fees_paid)} hint="cancellation fees" tone="text-rose-300" />
      </div>
    </section>
  )
}
