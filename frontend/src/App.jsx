import { useCallback, useEffect, useState } from 'react'
import { api } from './api.js'
import Dashboard from './components/Dashboard.jsx'
import VoiceBooking from './components/VoiceBooking.jsx'
import SafetyPanel from './components/SafetyPanel.jsx'
import FailureDemo from './components/FailureDemo.jsx'
import Exposure from './components/Exposure.jsx'
import Ledger from './components/Ledger.jsx'
import McpTrace from './components/McpTrace.jsx'

const NAV = [
  ['dashboard', 'Dashboard'],
  ['voice', 'Voice Booking'],
  ['safety', 'Transaction Safety'],
  ['exposure', 'Cancellation Exposure'],
  ['ledger', 'Ledger'],
]

const WELCOME = {
  who: 'bot',
  text: "Hi, I'm Reversa. Tell me where you'd like to go and your budget, e.g. \"Plan a Goa trip under ₹20,000.\" I will never spend money without your explicit approval of the exact amount.",
}

export default function App() {
  const [state, setState] = useState(null)
  const [tools, setTools] = useState([])
  const [messages, setMessages] = useState([WELCOME])
  const [approval, setApproval] = useState(null) // last {txn_id, token, claims, action}
  const [lastResult, setLastResult] = useState(null) // last safety result {ok, message, safety}
  const [busy, setBusy] = useState(false)
  const [speak, setSpeak] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try {
      setState(await api.state())
      setError('')
    } catch (e) {
      setError('Cannot reach the backend. Is `uvicorn app:app` running on :8000?')
    }
  }, [])

  useEffect(() => {
    refresh()
    api.tools().then(setTools).catch(() => {})
  }, [refresh])

  const push = (m) => setMessages((ms) => [...ms, m])

  const voiceOut = (text) => {
    if (!speak || !window.speechSynthesis) return
    window.speechSynthesis.cancel()
    const u = new SpeechSynthesisUtterance(text)
    u.lang = 'en-IN'
    window.speechSynthesis.speak(u)
  }

  // One conversational turn: text goes to the backend, which parses it and calls MCP tools.
  const say = async (text, extra = {}) => {
    if (!text.trim()) return null
    push({ who: 'user', text: extra.simulated_from || text, heard: extra.simulated_from ? text : null })
    setBusy(true)
    try {
      const r = await api.say(text, extra)
      push({ who: 'bot', text: r.reply, ok: r.ok, blocked: r.blocked })
      voiceOut(r.reply)
      if (r.token) setApproval({ txn_id: r.txn_id, token: r.token, claims: null, action: 'commit' })
      if (r.safety) setLastResult({ txn_id: r.txn_id || r.state?.active?.transaction?.txn_id, ok: r.ok, message: r.reply, safety: r.safety })
      setState(r.state)
      return r
    } catch (e) {
      push({ who: 'bot', text: String(e.message), ok: false })
    } finally {
      setBusy(false)
    }
    return null
  }

  // Generic wrapper for button actions that return {…, state}
  const act = async (fn, label) => {
    setBusy(true)
    try {
      const r = await fn()
      if (r.state) setState(r.state)
      else if (r.dashboard) setState(r)
      if (label) push({ who: 'system', text: label(r), ok: r.ok !== false })
      return r
    } catch (e) {
      push({ who: 'bot', text: String(e.message), ok: false })
    } finally {
      setBusy(false)
    }
    return null
  }

  const ctx = { state, say, act, busy, approval, setApproval, lastResult, setLastResult, push, refresh }

  return (
    <div className="min-h-screen bg-[radial-gradient(ellipse_at_top,rgba(124,140,255,0.08),transparent_55%)]">
      <header className="sticky top-0 z-20 border-b border-ink-700/70 bg-ink-950/85 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-3 sm:px-6">
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="grid h-8 w-8 place-items-center rounded-lg bg-brand/15 ring-1 ring-brand/40">
              <span className="text-sm font-bold text-brand">R</span>
            </div>
            <div className="leading-tight">
              <div className="font-semibold tracking-tight">Reversa</div>
              <div className="hidden text-[11px] text-ink-400 sm:block">AI agents should not spend money without explicit user authorisation</div>
            </div>
          </div>
          <nav className="ml-auto hidden gap-0.5 xl:flex">
            {NAV.map(([id, label]) => (
              <a key={id} href={`#${id}`} className="whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm text-ink-300 hover:bg-ink-800 hover:text-ink-100">
                {label}
              </a>
            ))}
          </nav>
          <div className="ml-auto flex shrink-0 items-center gap-2 xl:ml-2">
            <span className={`hidden items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs sm:inline-flex whitespace-nowrap ${tools.length ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300' : 'border-ink-600 text-ink-400'}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${tools.length ? 'bg-emerald-400' : 'bg-ink-400'}`} />
              MCP · {tools.length} tools
            </span>
            <button
              className="btn-ghost whitespace-nowrap !py-1.5"
              disabled={busy}
              onClick={async () => {
                await act(api.reset)
                setMessages([WELCOME])
                setApproval(null)
                setLastResult(null)
              }}
            >
              Reset demo
            </button>
          </div>
        </div>
      </header>

      {error && (
        <div className="mx-auto mt-4 max-w-7xl px-4 sm:px-6">
          <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">{error}</div>
        </div>
      )}

      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6">
        <Dashboard state={state} />

        <div className="grid gap-6 lg:grid-cols-12">
          <div className="min-w-0 lg:col-span-7">
            <VoiceBooking {...ctx} messages={messages} speak={speak} setSpeak={setSpeak} />
          </div>
          <div className="min-w-0 space-y-6 lg:col-span-5">
            <SafetyPanel {...ctx} />
            <FailureDemo {...ctx} />
          </div>
        </div>

        <Exposure state={state} />

        <div className="grid gap-6 xl:grid-cols-12">
          <div className="min-w-0 xl:col-span-8">
            <Ledger {...ctx} />
          </div>
          <div className="min-w-0 xl:col-span-4">
            <McpTrace state={state} tools={tools} />
          </div>
        </div>

        <footer className="pb-8 pt-2 text-center text-xs text-ink-400">
          Reversa prototype · Team Goal Diggers · wHACKiest 2026 Track 1 · simulated suppliers, real MCP, real signatures, real ledger
        </footer>
      </main>
    </div>
  )
}
