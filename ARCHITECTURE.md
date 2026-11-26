# Reversa architecture

```
 Browser (React + Vite + Tailwind)
   │  speech → text (Web Speech API) or typed text
   │
   │  /api/say, /api/hold, /api/commit, /api/cancel        /api/approve  (human only)
   ▼                                                        │
 FastAPI backend (app.py)                                   │
   ├─ speech.parse()  → structured intent                   │
   ├─ McpBridge  ── MCP ClientSession (JSON-RPC) ──┐        │
   │                                               ▼        │
   │                     FastMCP server (mcp_server.py)     │
   │                     quote_booking · hold_booking       │
   │                     commit_booking · cancel_booking    │
   │                                               │        │
   └───────────────────────────────────────────────┼────────┘
                                                   ▼
                        BookingService (reversa/service.py)
              ┌──────────────┬──────────────┬──────────────┬─────────────┐
              │ catalog.py   │ safety.py    │ approval.py  │ ledger.py   │
              │ simulated    │ 5 determin-  │ HMAC tokens, │ SQLite:     │
              │ supplier +   │ istic checks │ secret stays │ events,     │
              │ fee curves   │              │ server-side  │ transactions│
              └──────────────┴──────────────┴──────────────┴ approvals ──┘
                         solver.py: OR-Tools CP-SAT option chooser
```

## Booking lifecycle

```
quote ──► HELD ──approve(amount ok)──► token ──commit_booking──► COMMITTED ──approve(fee)──► cancel ──► CANCELLED
            │  └─approve(amount wrong)──► BLOCKED (tokens revoked) ──approve(correct)──► HELD
            └─cancel_booking (free) ──► RELEASED
```

| Event | Postings (paise, sum = 0) |
|---|---|
| wallet_funded | funding −X, free +X |
| hold | free −A, held +A |
| release | held −A, free +A |
| commit | held −A, committed +A |
| cancel | committed −A, fees +F, refund_pending +(A−F) |
| refund_settled | refund_pending −R, free +R |
| quote / approval_issued / approval_refused / commit_rejected | (no money moves: audit only) |

The "available" figure is `free`. A pending refund sits in `refund_pending` and only becomes `free` after
`refund_settled`, so it can never be spent early.

## The five safety checks (`reversa/safety.py`)

1. **Within budget:** committed spend in this trip + this amount ≤ the trip budget.
2. **Cancellation fee known:** the supplier published a policy (Sky Charter didn't, so it is always blocked).
3. **Exact amount confirmed:** the token's amount equals the transaction amount *and* the supplier's current price.
4. **Explicit approval:** the token's HMAC signature is valid; it is bound to this txn, action and supplier; it hasn't
   expired; and it is ACTIVE in the `approvals` table (not used, not revoked).
5. **Not already executed:** the transaction isn't already COMMITTED or CANCELLED. The ledger's idempotency key
   (`commit:<txn>`) is a second line of defence.

## Where the trust boundary is

- **MCP tools** can quote, hold, and *attempt* commit or cancel. They cannot create approvals.
- **`/api/approve`** is the only code path that signs a token. It runs only when the human states an amount that
  exactly matches. A mismatch blocks the transaction and revokes any earlier tokens.
- The **signing secret** lives in the backend process (env var or a git-ignored file). It is never sent to the
  browser and is not reachable through MCP.
- An LLM connected over MCP therefore cannot spend money. Even a forged, replayed, expired or edited token fails
  check 4.
