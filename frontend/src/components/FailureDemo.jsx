import { useRef, useState } from 'react'
import { api } from '../api.js'
import { Section } from './ui.jsx'

const wait = (ms) => new Promise((r) => setTimeout(r, ms))

export default function FailureDemo({ state, say, act, busy, approval, setLastResult }) {
  const [done, setDone] = useState({})
  const mark = (k) => setDone((d) => ({ ...d, [k]: true }))
  // refs so 'Run all' sees the latest state/approval between awaited steps
  const stRef = useRef(state)
  const apRef = useRef(approval)
  stRef.current = state
  apRef.current = approval

  const steps = [
    {
      k: 'hold',
      title: 'Select the ₹14,000 flight',
      detail: 'quote_booking + hold_booking via MCP',
      run: async () => {
        let st = stRef.current
        if (!st?.trip) st = (await say('Plan a Goa trip under ₹20,000'))?.state
        const active = st?.active?.transaction
        if (!active || active.option_id !== 'fl_goa_flexi') await say('Book the ₹14,000 flight')
      },
    },
    {
      k: 'mishear',
      title: 'User says “Book the ₹14,000 flight”, recogniser outputs ₹40,000',
      detail: 'safety engine blocks; earlier approvals revoked',
      run: () => say('Book the ₹40,000 flight', { simulated_from: 'Book the ₹14,000 flight' }),
    },
    {
      k: 'correct',
      title: 'User corrects: “confirm 14,000”',
      detail: 'fresh signed approval → commit_booking',
      run: () => say('confirm 14000'),
    },
    {
      k: 'dup',
      title: 'Replay the same commit (duplicate request)',
      detail: 'same token, same transaction → rejected',
      run: async () => {
        await wait(50)
        const a = apRef.current
        if (!a) return
        const r = await act(() => api.commit(a.txn_id, a.token), (r) => `Duplicate commit_booking → ${r.ok ? r.message : r.error}`)
        if (r) setLastResult({ txn_id: a.txn_id, ok: r.ok, message: r.ok ? r.message : r.error, safety: r.safety })
      },
    },
  ]

  const runAll = async () => {
    for (const s of steps) {
      await s.run()
      mark(s.k)
      await wait(600)
    }
  }

  return (
    <Section
      title="Failure demonstration"
      subtitle="Speech misrecognition and duplicate execution, run against the real backend."
      right={<button className="btn-ghost !py-1.5 text-xs" disabled={busy} onClick={runAll}>Run all</button>}
    >
      <ol className="space-y-2">
        {steps.map((s, i) => (
          <li key={s.k} className="flex items-center gap-3 rounded-lg border border-ink-700 bg-ink-850/50 p-2.5">
            <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs ${done[s.k] ? 'bg-emerald-500/20 text-emerald-300' : 'bg-ink-700 text-ink-300'}`}>
              {done[s.k] ? '✓' : i + 1}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm">{s.title}</div>
              <div className="text-xs text-ink-400">{s.detail}</div>
            </div>
            <button
              className="btn-ghost shrink-0 !px-2.5 !py-1 text-xs"
              disabled={busy || (s.k === 'dup' && !approval)}
              onClick={async () => {
                await s.run()
                mark(s.k)
              }}
            >
              Run
            </button>
          </li>
        ))}
      </ol>
    </Section>
  )
}
