import { Badge, Section } from './ui.jsx'

export default function McpTrace({ state, tools }) {
  const trace = state?.mcp_trace || []
  return (
    <Section title="MCP activity" subtitle="Live tools/call requests from the backend's MCP client to the FastMCP server.">
      <div className="flex flex-wrap gap-1.5">
        {tools.map((t) => (
          <span key={t.name} title={t.description} className="num rounded-md border border-ink-700 bg-ink-850 px-2 py-1 text-[11px] text-ink-300">
            {t.name}
          </span>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-ink-400">There is no approve tool: a model connected over MCP can request, never authorise.</p>
      <ol className="mt-4 max-h-[26rem] space-y-2 overflow-y-auto">
        {trace.length === 0 && <li className="text-sm text-ink-400">No MCP calls yet.</li>}
        {trace.map((c, i) => (
          <li key={i} className="rounded-lg border border-ink-700 bg-ink-950/50 p-2.5">
            <div className="flex items-center justify-between gap-2">
              <span className="num text-xs text-brand">{c.tool}</span>
              <span className="flex items-center gap-2">
                <span className="num text-[10px] text-ink-400">{c.ms} ms</span>
                <Badge tone={c.ok ? 'pass' : 'fail'}>{c.ok ? 'ok' : 'rejected'}</Badge>
              </span>
            </div>
            <div className="num mt-1 break-all text-[11px] text-ink-400">{JSON.stringify(c.args)}</div>
            {c.summary && <div className="mt-1 text-xs text-ink-300">{c.summary}</div>}
          </li>
        ))}
      </ol>
    </Section>
  )
}
