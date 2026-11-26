// Thin client for the FastAPI backend. All money from the API is integer paise.
async function req(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${method} ${path} → HTTP ${r.status}`)
  return r.json()
}
export const api = {
  state: () => req('GET', '/api/state'),
  tools: () => req('GET', '/api/mcp/tools'),
  say: (text, extra = {}) => req('POST', '/api/say', { text, ...extra }),
  hold: (option_id) => req('POST', '/api/hold', { option_id }),
  approve: (txn_id, amount_rupees, action = 'commit') => req('POST', '/api/approve', { txn_id, amount_rupees, action }),
  commit: (txn_id, token) => req('POST', '/api/commit', { txn_id, token }),
  cancel: (txn_id, token = '') => req('POST', '/api/cancel', { txn_id, token }),
  safety: (txn_id, action = 'commit') => req('GET', `/api/safety/${txn_id}?action=${action}`),
  compare: (hours) => req('GET', `/api/compare?hours=${hours}`),
  optimise: (p) => req('GET', `/api/optimise?p=${p}`),
  settle: () => req('POST', '/api/demo/settle'),
  reset: () => req('POST', '/api/demo/reset'),
}

export const inr = (paise) =>
  paise === null || paise === undefined
    ? '—'
    : '₹' + (paise / 100).toLocaleString('en-IN', { maximumFractionDigits: paise % 100 ? 2 : 0 })
