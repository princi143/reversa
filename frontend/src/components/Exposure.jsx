import { useEffect, useState } from 'react'
import { api, inr } from '../api.js'
import { Badge, Section } from './ui.jsx'

export default function Exposure({ state }) {
  const [hours, setHours] = useState(240)
  const [rows, setRows] = useState([])
  const [cat, setCat] = useState('flight')
  const [p, setP] = useState(30)
  const [opt, setOpt] = useState(null)
  const tripId = state?.trip?.trip_id

  useEffect(() => {
    if (!tripId) return setRows([])
    api.compare(hours).then((r) => setRows(r.options)).catch(() => {})
  }, [hours, tripId])

  useEffect(() => {
    if (!tripId) return setOpt(null)
    const t = setTimeout(() => api.optimise(p).then(setOpt).catch(() => {}), 120)
    return () => clearTimeout(t)
  }, [p, tripId])

  const shown = rows.filter((r) => r.category === cat)
  const max = Math.max(1, ...shown.map((r) => r.price))
  const committed = (state?.transactions || []).filter((t) => t.status === 'COMMITTED')
  const totalFee = committed.reduce((s, t) => s + (t.cancellation_fee || 0), 0)
  const days = hours / 24

  return (
    <Section
      id="exposure"
      title="Cancellation Exposure"
      subtitle="What you could lose if you cancel, shown before you approve anything."
      right={
        <div className="text-right">
          <div className="label">Your exposure right now</div>
          <div className="num text-lg font-semibold text-rose-300">{inr(totalFee)}</div>
          <div className="text-[11px] text-ink-400">{committed.length} committed booking(s)</div>
        </div>
      }
    >
      {!tripId ? (
        <p className="text-sm text-ink-400">Request a trip to compare options.</p>
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex rounded-lg border border-ink-700 p-0.5 text-xs">
                {['flight', 'hotel'].map((c) => (
                  <button key={c} onClick={() => setCat(c)} className={`rounded-md px-3 py-1 capitalize ${cat === c ? 'bg-ink-700 text-ink-100' : 'text-ink-400'}`}>
                    {c}s
                  </button>
                ))}
              </div>
              <div className="text-xs text-ink-400">
                If cancelled <span className="num text-ink-100">{days % 1 ? days.toFixed(1) : days}</span> day(s) before departure
              </div>
            </div>
            <input type="range" min={0} max={240} step={6} value={hours} onChange={(e) => setHours(+e.target.value)} className="mt-3 w-full" />
            <div className="flex justify-between text-[10px] text-ink-400">
              <span>departure</span><span>3 days</span><span>7 days</span><span>10 days (today)</span>
            </div>
            <div className="mt-4 space-y-3">
              {shown.map((r) => (
                <div key={r.option_id}>
                  <div className="mb-1 flex items-baseline justify-between gap-2 text-sm">
                    <span className="truncate">{r.name}</span>
                    <span className="num shrink-0 text-xs text-ink-300">
                      {r.known ? (
                        <>
                          lose <span className="text-rose-300">{inr(r.fee)}</span> · back <span className="text-emerald-300">{inr(r.refundable)}</span>
                        </>
                      ) : (
                        <span className="text-amber-300">fee unknown</span>
                      )}
                    </span>
                  </div>
                  <div className="flex h-3 overflow-hidden rounded-full bg-ink-800" style={{ width: `${(r.price / max) * 100}%` }}>
                    {r.known ? (
                      <>
                        <div className="bg-rose-500/80 transition-all" style={{ width: `${(r.fee / r.price) * 100}%` }} />
                        <div className="bg-emerald-500/70 transition-all" style={{ width: `${(r.refundable / r.price) * 100}%` }} />
                      </>
                    ) : (
                      <div className="w-full bg-[repeating-linear-gradient(45deg,rgba(245,158,11,.35)_0_6px,transparent_6px_12px)]" />
                    )}
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-3 flex gap-4 text-[11px] text-ink-400">
              <span><span className="mr-1 inline-block h-2 w-2 rounded-sm bg-rose-500/80" />cancellation fee</span>
              <span><span className="mr-1 inline-block h-2 w-2 rounded-sm bg-emerald-500/70" />refundable</span>
              <span><span className="mr-1 inline-block h-2 w-2 rounded-sm bg-amber-500/50" />unknown policy</span>
            </div>
          </div>

          <div className="rounded-xl border border-ink-700 bg-ink-850/40 p-4">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium">CP-SAT recommendation</div>
                <div className="text-xs text-ink-400">minimise price + p × cancellation fee, total ≤ budget</div>
              </div>
              <Badge tone="info">OR-Tools</Badge>
            </div>
            <label className="mt-3 block text-xs text-ink-300">
              Chance you'll cancel: <span className="num text-ink-100">{p}%</span>
              <input type="range" min={0} max={100} step={5} value={p} onChange={(e) => setP(+e.target.value)} className="mt-1 w-full" />
            </label>
            {opt && (
              <>
                <table className="mt-3 w-full text-xs">
                  <thead className="text-ink-400">
                    <tr><th className="py-1 text-left font-normal">Option</th><th className="text-right font-normal">Price</th><th className="text-right font-normal">Fee now</th><th className="text-right font-normal">Risk cost</th></tr>
                  </thead>
                  <tbody className="num">
                    {opt.table.map((r) => (
                      <tr key={r.id} className={`border-t border-ink-700/60 ${r.chosen ? 'text-emerald-300' : r.eligible ? '' : 'text-ink-400 line-through'}`}>
                        <td className="py-1.5 pr-2 font-sans">{r.chosen && '✓ '}{r.name}</td>
                        <td className="text-right">{inr(r.price)}</td>
                        <td className="text-right">{r.fee == null ? '?' : inr(r.fee)}</td>
                        <td className="text-right">{r.risk_cost == null ? '—' : inr(r.risk_cost)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className={`mt-3 text-xs leading-relaxed ${opt.ok ? 'text-ink-300' : 'text-rose-300'}`}>{opt.explanation}</p>
              </>
            )}
          </div>
        </div>
      )}
    </Section>
  )
}
