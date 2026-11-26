import { useEffect, useState } from 'react'
import { api, inr } from '../api.js'
import { Badge, CheckIcon, Section, STATUS_TONE } from './ui.jsx'

export function decodeClaims(token) {
  try {
    const b = token.split('.')[0].replace(/-/g, '+').replace(/_/g, '/')
    return JSON.parse(atob(b + '='.repeat((4 - (b.length % 4)) % 4)))
  } catch {
    return null
  }
}

export default function SafetyPanel({ state, act, busy, approval, setApproval, lastResult, setLastResult }) {
  const [amount, setAmount] = useState('')
  const [preview, setPreview] = useState(null)
  const [now, setNow] = useState(Date.now() / 1000)
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(t)
  }, [])

  const txns = state?.transactions || []
  const byId = (id) => txns.find((t) => t.txn_id === id)
  const pc = state?.pending_cancel && byId(state.pending_cancel)
  const cancelMode = pc && pc.status === 'COMMITTED'
  const target = cancelMode ? pc : state?.active?.transaction || (approval && byId(approval.txn_id)) || null
  const action = cancelMode ? 'cancel' : 'commit'
  const expected = target ? (action === 'commit' ? target.amount : target.cancellation_fee) : null

  // Preview checks without a token (cancel mode needs its own fetch).
  useEffect(() => {
    if (!target) return setPreview(null)
    api.safety(target.txn_id, action).then((r) => setPreview(r.safety)).catch(() => {})
  }, [target?.txn_id, target?.status, action, state?.events?.length])

  const mine = (r) => r && target && r.txn_id === target.txn_id
  const checks = (mine(lastResult) && lastResult.safety) || preview || state?.active?.safety || []
  const tok = approval && target && approval.txn_id === target.txn_id && approval.action === action ? approval : null
  const claims = tok ? decodeClaims(tok.token) : null
  const tokenRow = claims && state?.approvals?.find((a) => a.nonce === claims.nonce)
  const passed = checks.filter((c) => c.status === 'pass').length

  const doApprove = async () => {
    const r = await act(
      () => api.approve(target.txn_id, amount, action),
      (r) => (r.ok ? `Approval signed for ${inr(r.claims.amount)} (${action}) · expires in 120s` : r.message),
    )
    if (!r) return
    setLastResult({ txn_id: target.txn_id, ok: r.ok, message: r.ok ? 'Approved. Signed token minted by the backend.' : r.message, safety: r.safety })
    if (r.ok) setApproval({ txn_id: target.txn_id, token: r.token, action })
    setAmount('')
  }

  const doExecute = async () => {
    const call = action === 'commit' ? () => api.commit(target.txn_id, tok?.token || '') : () => api.cancel(target.txn_id, tok?.token || '')
    const r = await act(call, (r) => `MCP ${action}_booking → ${r.ok ? r.message : r.error}`)
    if (r) setLastResult({ txn_id: target.txn_id, ok: r.ok, message: r.ok ? r.message : r.error, safety: r.safety })
  }

  return (
    <Section
      id="safety"
      title="Transaction Safety"
      subtitle="Deterministic checks in the backend. No model output can pass them."
      right={target && <Badge tone={passed === 5 ? 'pass' : checks.some((c) => c.status === 'fail') ? 'fail' : 'pending'}>{passed}/5 checks</Badge>}
    >
      {!target ? (
        <p className="rounded-lg border border-dashed border-ink-600 p-4 text-sm text-ink-400">
          Nothing is awaiting approval. Select a booking option (or say “Book the ₹14,000 flight”) to place a hold.
        </p>
      ) : (
        <div className="space-y-4">
          <div className="rounded-xl border border-ink-700 bg-ink-850/60 p-3">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="label">{action === 'commit' ? 'Booking to commit' : 'Cancellation to approve'}</div>
                <div className="truncate text-sm font-medium">{target.name}</div>
              </div>
              <Badge tone={STATUS_TONE[target.status]}>{target.status}</Badge>
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
              <dt className="text-ink-400">Transaction</dt><dd className="num text-right">{target.txn_id}</dd>
              <dt className="text-ink-400">Supplier</dt><dd className="text-right">{target.supplier}</dd>
              <dt className="text-ink-400">Amount</dt><dd className="num text-right font-semibold">{inr(target.amount)}</dd>
              <dt className="text-ink-400">Cancellation fee now</dt><dd className="num text-right text-rose-300">{target.cancellation_fee == null ? 'unknown' : inr(target.cancellation_fee)}</dd>
              <dt className="text-ink-400">Refundable if cancelled</dt><dd className="num text-right text-emerald-300">{target.cancellation_fee == null ? 'unknown' : inr(target.amount - target.cancellation_fee)}</dd>
            </dl>
            {target.note && <div className="mt-2 text-xs text-ink-400">{target.note}</div>}
          </div>

          <ul className="space-y-2">
            {checks.map((c, i) => (
              <li key={c.id} className="flex gap-2.5">
                <CheckIcon status={c.status} />
                <div className="min-w-0">
                  <div className="text-sm"><span className="text-ink-400">{i + 1}.</span> {c.name}</div>
                  <div className="text-xs text-ink-400">{c.detail}</div>
                </div>
              </li>
            ))}
          </ul>

          {mine(lastResult) && lastResult.message && (
            <div className={`rounded-lg border px-3 py-2 text-sm ${lastResult.ok ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200' : 'border-rose-500/40 bg-rose-500/10 text-rose-200'}`}>
              {lastResult.message}
            </div>
          )}

          {['HELD', 'BLOCKED'].includes(target.status) || cancelMode ? (
            <div className="space-y-2 rounded-xl border border-ink-700 p-3">
              <div className="label">Step 1 · Explicit approval (human only, not an MCP tool)</div>
              <div className="flex gap-2">
                <div className="relative min-w-0 flex-1">
                  <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-ink-400">₹</span>
                  <input
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                    inputMode="numeric"
                    placeholder={`type the exact ${action === 'commit' ? 'amount' : 'fee'}${expected != null ? ` (${expected / 100})` : ''}`}
                    className="num w-full rounded-lg border border-ink-600 bg-ink-850 py-2 pl-7 pr-3 text-sm outline-none placeholder:font-sans placeholder:text-ink-400 focus:border-brand"
                  />
                </div>
                <button className="btn-primary shrink-0" disabled={busy || !amount} onClick={doApprove}>
                  Approve & sign
                </button>
              </div>
              <div className="label pt-1">Step 2 · Execute through MCP</div>
              <button className={action === 'commit' ? 'btn-ghost w-full' : 'btn-danger w-full'} disabled={busy} onClick={doExecute}>
                {action === 'commit' ? 'Call commit_booking' : 'Call cancel_booking'} {tok ? 'with signed token' : 'without approval (will be blocked)'}
              </button>
            </div>
          ) : (
            target.status === 'COMMITTED' && tok && (
              <button className="btn-danger w-full" disabled={busy} onClick={doExecute}>
                Replay the same commit_booking (duplicate test)
              </button>
            )
          )}

          {tok && claims && (
            <div className="rounded-xl border border-ink-700 bg-ink-950/60 p-3">
              <div className="flex items-center justify-between">
                <div className="label">Signed approval token (HMAC-SHA256)</div>
                <Badge tone={STATUS_TONE[tokenRow?.status] || 'neutral'}>
                  {tokenRow?.status || '—'}
                  {tokenRow?.status === 'ACTIVE' && ` · ${Math.max(0, Math.round(claims.exp - now))}s`}
                </Badge>
              </div>
              <div className="num mt-2 break-all text-[11px] text-ink-400">{tok.token.slice(0, 64)}…</div>
              <dl className="mt-2 grid grid-cols-2 gap-y-1 text-xs">
                {[['Transaction', claims.txn], ['Amount', inr(claims.amount)], ['Supplier', claims.supplier], ['Action', claims.action],
                  ['Expires', new Date(claims.exp * 1000).toLocaleTimeString()], ['Nonce', claims.nonce]].map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-ink-400">{k}</dt>
                    <dd className="num text-right">{v}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-2 text-[11px] text-ink-400">The signing secret stays in the backend. Changing any field invalidates the signature.</p>
            </div>
          )}
        </div>
      )}
    </Section>
  )
}
