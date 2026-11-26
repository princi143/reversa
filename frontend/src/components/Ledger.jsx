import { api, inr } from '../api.js'
import { Badge, CheckIcon, Section, STATUS_TONE } from './ui.jsx'

const STATES = [
  ['free', 'Available', 'bg-emerald-500/70'],
  ['held', 'Held', 'bg-amber-500/70'],
  ['committed', 'Committed', 'bg-brand'],
  ['refund_pending', 'Refund pending', 'bg-sky-500/70'],
  ['fees', 'Fees', 'bg-rose-500/70'],
]

const fmtPostings = (p) =>
  Object.entries(p)
    .map(([k, v]) => `${k} ${v > 0 ? '+' : '−'}${inr(Math.abs(v)).slice(1)}`)
    .join(', ')

export default function Ledger({ state, act, busy }) {
  const rec = state?.reconcile
  const bal = rec?.balances || {}
  const total = rec?.funded || 1
  const txns = state?.transactions || []
  const events = [...(state?.events || [])].reverse()

  const cancel = async (t) => {
    const r = await act(() => api.cancel(t.txn_id), (r) => `MCP cancel_booking → ${r.ok ? r.message : r.error}`)
    if (r?.needs_approval) document.getElementById('safety')?.scrollIntoView()
  }

  return (
    <Section
      id="ledger"
      title="Ledger"
      subtitle="SQLite, integer paise, append-only. Balances are recomputed from events, never stored."
      right={
        <button className="btn-ghost !py-1.5 text-xs" disabled={busy || !bal.refund_pending} onClick={() => act(api.settle, () => 'Bank settled pending refunds → now available')}>
          Bank settles refunds
        </button>
      }
    >
      <div className="flex h-2.5 overflow-hidden rounded-full bg-ink-800">
        {STATES.map(([k, , c]) => (
          <div key={k} className={`${c} transition-all`} style={{ width: `${((bal[k] || 0) / total) * 100}%` }} />
        ))}
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-5">
        {STATES.map(([k, label, c]) => (
          <div key={k} className="text-xs">
            <div className="flex items-center gap-1.5 text-ink-400"><span className={`h-2 w-2 rounded-sm ${c}`} />{label}</div>
            <div className="num mt-0.5 text-sm">{inr(bal[k] || 0)}</div>
          </div>
        ))}
      </div>

      <div className="label mb-2 mt-6">Transactions</div>
      <div className="overflow-x-auto rounded-xl border border-ink-700">
        <table className="w-full min-w-[760px] text-xs">
          <thead className="bg-ink-850 text-left text-ink-400">
            <tr>
              {['Booking', 'Transaction', 'Item', 'Amount', 'Cancel fee', 'Committed', 'Refund pending', 'Status', ''].map((h) => (
                <th key={h} className="px-3 py-2 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="num whitespace-nowrap">
            {txns.length === 0 && (
              <tr><td colSpan={9} className="px-3 py-4 text-center font-sans text-ink-400">No transactions yet.</td></tr>
            )}
            {txns.map((t) => (
              <tr key={t.txn_id} className="border-t border-ink-700/70">
                <td className="px-3 py-2">{t.booking_id}</td>
                <td className="px-3 py-2">{t.txn_id}</td>
                <td className="max-w-[180px] truncate px-3 py-2 font-sans" title={t.name}>{t.name}</td>
                <td className="px-3 py-2">{inr(t.amount)}</td>
                <td className="px-3 py-2">{t.cancellation_fee == null ? 'unknown' : inr(t.cancellation_fee)}</td>
                <td className="px-3 py-2">{inr(t.committed_amount)}</td>
                <td className="px-3 py-2 text-sky-300">{inr(t.refund_pending)}</td>
                <td className="px-3 py-2"><Badge tone={STATUS_TONE[t.status]}>{t.status}</Badge></td>
                <td className="px-3 py-2 text-right font-sans">
                  {['HELD', 'BLOCKED'].includes(t.status) && (
                    <button className="text-ink-300 underline-offset-2 hover:underline" disabled={busy} onClick={() => cancel(t)}>Release</button>
                  )}
                  {t.status === 'COMMITTED' && (
                    <button className="text-rose-300 underline-offset-2 hover:underline" disabled={busy} onClick={() => cancel(t)}>Cancel…</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-5">
        <div className="min-w-0 lg:col-span-3">
          <div className="label mb-2">Transaction history (event log)</div>
          <div className="max-h-80 overflow-auto rounded-xl border border-ink-700">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-ink-850 text-left text-ink-400">
                <tr><th className="px-3 py-2 font-medium">#</th><th className="px-3 py-2 font-medium">Event</th><th className="px-3 py-2 font-medium">Postings</th><th className="px-3 py-2 text-right font-medium">Available after</th></tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.id} className="border-t border-ink-700/70 align-top">
                    <td className="num px-3 py-1.5 text-ink-400">{e.id}</td>
                    <td className="px-3 py-1.5">
                      <span className={/rejected|refused/.test(e.type) ? 'text-rose-300' : ''}>{e.type}</span>
                      {e.txn_id && <span className="num ml-1 text-ink-400">{e.txn_id}</span>}
                      {e.payload?.reasons && <div className="text-[11px] text-ink-400">{e.payload.reasons.join(' · ')}</div>}
                      {e.type === 'approval_refused' && e.payload?.heard != null && (
                        <div className="text-[11px] text-ink-400">heard {inr(e.payload.heard)}, expected {inr(e.payload.expected)}</div>
                      )}
                    </td>
                    <td className="num px-3 py-1.5 text-ink-300">{Object.keys(e.postings).length ? fmtPostings(e.postings) : <span className="text-ink-600">no money moved</span>}</td>
                    <td className="num px-3 py-1.5 text-right">{inr(e.available_after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="min-w-0 lg:col-span-2">
          <div className="mb-2 flex items-center justify-between">
            <div className="label">Reconciliation</div>
            {rec && <Badge tone={rec.ok ? 'pass' : 'fail'}>{rec.ok ? 'balanced' : 'mismatch'}</Badge>}
          </div>
          <ul className="space-y-2">
            {rec?.checks.map((c) => (
              <li key={c.name} className="flex gap-2.5">
                <CheckIcon status={c.ok ? 'pass' : 'fail'} />
                <div>
                  <div className="text-xs">{c.name}</div>
                  {c.detail && <div className="num text-[11px] text-ink-400">{c.detail}</div>}
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Section>
  )
}
