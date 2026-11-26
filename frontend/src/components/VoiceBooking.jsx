import { useEffect, useRef, useState } from 'react'
import { api, inr } from '../api.js'
import { Badge, Section } from './ui.jsx'

const SUGGESTIONS = ['Plan a Goa trip under ₹20,000', 'Book the ₹14,000 flight', 'confirm 14000', 'Recommend the safest option', 'Show the ledger']

export default function VoiceBooking({ state, say, act, busy, messages, speak, setSpeak, push }) {
  const [text, setText] = useState('')
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState('')
  const [options, setOptions] = useState([])
  const recRef = useRef(null)
  const endRef = useRef(null)
  const SR = typeof window !== 'undefined' && (window.SpeechRecognition || window.webkitSpeechRecognition)

  const tripId = state?.trip?.trip_id
  useEffect(() => {
    if (!tripId) return setOptions([])
    api.compare(240).then((r) => setOptions(r.options)).catch(() => {})
  }, [tripId])

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [messages])

  const submit = async (t) => {
    setText('')
    await say(t)
  }

  const toggleMic = () => {
    if (!SR) return
    if (recRef.current) return recRef.current.stop()
    const rec = new SR()
    rec.lang = 'en-IN'
    rec.interimResults = true
    rec.onresult = (e) => {
      const r = e.results[e.results.length - 1]
      if (r.isFinal) submit(r[0].transcript)
      else setInterim(r[0].transcript)
    }
    rec.onerror = (e) => push({ who: 'bot', text: `Microphone error: ${e.error}. You can type instead.`, ok: false })
    rec.onend = () => {
      recRef.current = null
      setListening(false)
      setInterim('')
    }
    window.speechSynthesis?.cancel()
    recRef.current = rec
    setListening(true)
    rec.start()
  }

  const txns = state?.transactions || []
  const statusOf = (optionId) =>
    [...txns].reverse().find((t) => t.option_id === optionId && t.trip_id === tripId && ['HELD', 'BLOCKED', 'COMMITTED'].includes(t.status))?.status

  const select = (o) =>
    act(() => api.hold(o.option_id), (r) => (r.ok ? `MCP hold_booking → held ${o.name} for ${inr(o.price)} (${r.transaction.txn_id}). Approve it in Transaction Safety.` : `MCP hold_booking → ${r.error}`))

  return (
    <Section
      id="voice"
      title="Voice Booking"
      subtitle="Speak or type. Requests are parsed into structured intent, then executed through MCP tools."
      right={
        <label className="flex cursor-pointer items-center gap-2 text-xs text-ink-300">
          <input type="checkbox" checked={speak} onChange={(e) => setSpeak(e.target.checked)} className="accent-[#7c8cff]" />
          Read replies aloud
        </label>
      }
    >
      <div className="flex h-80 flex-col gap-2.5 overflow-y-auto rounded-xl border border-ink-700 bg-ink-950/60 p-3">
        {messages.map((m, i) => (
          <Message key={i} m={m} />
        ))}
        {interim && <div className="self-end text-sm italic text-ink-400">{interim}…</div>}
        <div ref={endRef} />
      </div>

      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          submit(text)
        }}
      >
        <button
          type="button"
          onClick={toggleMic}
          disabled={!SR}
          title={SR ? 'Speak (en-IN)' : 'Speech recognition needs Chrome or Edge. Type instead.'}
          className={`btn shrink-0 ${listening ? 'bg-rose-500 text-white' : 'border border-ink-600 bg-ink-850 hover:border-ink-400'}`}
        >
          <span className={`h-2 w-2 rounded-full ${listening ? 'animate-pulse bg-white' : 'bg-rose-400'}`} />
          {listening ? 'Listening…' : SR ? 'Speak' : 'No mic'}
        </button>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Type a request, e.g. Plan a Goa trip under ₹20,000"
          className="min-w-0 flex-1 rounded-lg border border-ink-600 bg-ink-850 px-3 py-2 text-sm outline-none placeholder:text-ink-400 focus:border-brand"
        />
        <button className="btn-primary shrink-0" disabled={busy || !text.trim()}>
          Send
        </button>
      </form>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {SUGGESTIONS.map((s) => (
          <button key={s} onClick={() => submit(s)} disabled={busy} className="rounded-full border border-ink-700 px-2.5 py-1 text-xs text-ink-300 hover:border-ink-400 hover:text-ink-100">
            {s}
          </button>
        ))}
      </div>

      {options.length > 0 && (
        <div className="mt-5">
          <div className="label mb-2">Booking options · from MCP quote_booking</div>
          <div className="grid gap-2 sm:grid-cols-2">
            {options.map((o) => {
              const st = statusOf(o.option_id)
              return (
                <div key={o.option_id} className={`rounded-xl border p-3 ${st ? 'border-brand/50 bg-brand/5' : 'border-ink-700 bg-ink-850/60'}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-[11px] uppercase tracking-wide text-ink-400">{o.category} · {o.supplier}</div>
                      <div className="truncate text-sm font-medium">{o.name}</div>
                    </div>
                    <div className="num text-right text-base font-semibold">{inr(o.price)}</div>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
                    {o.known ? (
                      <>
                        <Badge tone="fail">fee now {inr(o.fee)}</Badge>
                        <Badge tone="pass">refund {inr(o.refundable)}</Badge>
                      </>
                    ) : (
                      <Badge tone="pending">cancellation fee unknown</Badge>
                    )}
                    {!o.within_budget && <Badge tone="fail">over budget</Badge>}
                    <div className="ml-auto">
                      {st ? (
                        <Badge tone={st === 'COMMITTED' ? 'pass' : st === 'BLOCKED' ? 'fail' : 'pending'}>{st.toLowerCase()}</Badge>
                      ) : (
                        <button className="btn-ghost !px-2.5 !py-1 text-xs" disabled={busy} onClick={() => select(o)}>
                          Select & hold
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </Section>
  )
}

function Message({ m }) {
  if (m.who === 'user')
    return (
      <div className="max-w-[85%] self-end">
        <div className="rounded-2xl rounded-br-sm bg-brand/90 px-3.5 py-2 text-sm text-ink-950">{m.text}</div>
        {m.heard && (
          <div className="mt-1 text-right text-[11px] text-rose-300">
            speech recogniser output: “{m.heard}” <span className="text-ink-400">(simulated error)</span>
          </div>
        )}
      </div>
    )
  if (m.who === 'system')
    return (
      <div className={`self-center rounded-md border px-2.5 py-1 font-mono text-[11px] ${m.ok ? 'border-ink-700 text-ink-300' : 'border-rose-500/40 text-rose-300'}`}>
        {m.text}
      </div>
    )
  return (
    <div
      className={`max-w-[85%] self-start rounded-2xl rounded-bl-sm border px-3.5 py-2 text-sm ${
        m.blocked ? 'border-rose-500/50 bg-rose-500/10 text-rose-100' : m.ok === false ? 'border-amber-500/30 bg-ink-800 text-ink-100' : 'border-ink-700 bg-ink-800 text-ink-100'
      }`}
    >
      {m.blocked && <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-rose-300">Transaction stopped</div>}
      {m.text}
    </div>
  )
}
