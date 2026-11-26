# Reversa

**wHACKiest 2026 · Track 1: Make MCP Matter: Less Magic, More Utility** · Team Goal Diggers

Reversa is a voice-first AI travel booking assistant built on MCP. Before it spends any money it checks the
financial consequences, requires the user's explicit approval of the exact amount, controls how the
transaction executes, and handles failures safely.

> **AI agents should not be allowed to spend money without explicit user authorisation.**

This is an ideathon MVP. The suppliers are simulated. The MCP communication, approval signatures, safety
checks and SQLite ledger are real code, and the tests exercise them.

---

## Quick start

Requirements: **Python 3.10+**, **Node 18+**, and Chrome or Edge if you want to use the microphone.

```bash
# 1. backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. frontend (build once; FastAPI serves it)
cd frontend
npm install
npm run build
cd ..

# 3. run
uvicorn app:app --reload           # open http://localhost:8000
```

**Frontend dev mode with hot reload:** keep `uvicorn app:app --reload` running, then run `cd frontend && npm run dev`
and open http://localhost:5173. Vite proxies `/api` to port 8000.

**Tests:**

```bash
pytest -q                          # 27 tests
```

State lives in `reversa.db`. The signing secret is either `$REVERSA_SECRET` or a `.reversa_secret` file that is
generated on first run. Both are git-ignored. The **Reset demo** button wipes the database and funds a ₹50,000 demo
wallet, so you never have to edit the database by hand.

---

## Demo script (≈3 minutes)

| # | Do | What you see |
|---|----|---|
| 1 | Say or type **"Plan a Goa trip under ₹20,000"** | The intent is extracted (Goa, ₹20,000, 3 nights). MCP `quote_booking` returns 4 flights and 3 hotels with their cancellation fees. |
| 2 | **Select & hold** *Coastline Airways Flexi ₹14,000*, or say "Book the ₹14,000 flight" | MCP `hold_booking` moves ₹14,000 from *available* to *held*. The safety panel shows 3/5 checks passed and approval pending. |
| 3 | Click **Call commit_booking without approval** | The safety engine blocks it, and a `commit_rejected` event is written to the ledger. |
| 4 | Open **Cancellation Exposure** and drag the slider | Fee and refundable amount for each option, from 10 days out down to departure. Sky Charter shows the fee as *unknown*. |
| 5 | Move the **CP-SAT** cancel-risk slider | At 0% it picks the cheapest pair, at 30% Konkan Jet, at 100% the flexible fare. |
| 6 | In **Failure demonstration**, run steps 2–4 (or click **Run all**) | **Misheard ₹40,000:** *"I heard ₹40,000. Your intended booking amount was ₹14,000. I have stopped the transaction. Please confirm the correct amount."* The transaction becomes BLOCKED and the earlier approval token is revoked. **Corrected to ₹14,000:** a fresh token is signed, then `commit_booking` runs and all 5 checks pass. **Duplicate commit:** rejected. |
| 7 | **Ledger** | Transactions table, event log with the running available balance, and 7 reconciliation checks. Cancel the booking (approve the ₹1,400 fee) and ₹12,600 moves to *refund pending*, which is **not** counted as available until you click **Bank settles refunds**. |
| 8 | Try **"Book the ₹8,700 hotel"**, then "confirm 8700" | Budget violation: ₹14,000 + ₹8,700 > ₹20,000. The backend refuses to even mint an approval. |

---

## What each feature maps to

| Feature | Where | Notes |
|---|---|---|
| 1. MCP booking: `quote_booking`, `hold_booking`, `commit_booking`, `cancel_booking` | `mcp_server.py` | FastMCP server. The web backend calls it through an MCP `ClientSession` (in-memory transport). It also runs standalone over stdio. |
| 2. Voice booking | `frontend/src/components/VoiceBooking.jsx`, `reversa/speech.py` | Browser Web Speech API (en-IN) with a text fallback. Rule-based intent and number extraction, e.g. "fourteen thousand" → ₹14,000. |
| 3. Financial safety engine (5 checks) | `reversa/safety.py` | A pure, deterministic function. Results are shown live in the UI. |
| 4. Signed approval token | `reversa/approval.py`, `BookingService.approve()` | HMAC-SHA256 over {txn, amount, supplier, action, exp, nonce}. 120 s expiry, single use, revocable. |
| 5. Cancellation exposure | `reversa/catalog.py` (`cancel_fee`, `exposure`), `Exposure.jsx` | Time-dependent fee curves in integer paise. |
| 6. Ledger | `reversa/ledger.py` | SQLite. Append-only zero-sum event log, a `transactions` table and an `approvals` table. Balances are replayed from events. |
| 7. Failure demo | `BookingService.approve()` mismatch path, `FailureDemo.jsx` | Misrecognition block, revocation, fresh approval and duplicate rejection. |
| 8. Optimisation | `reversa/solver.py` | OR-Tools CP-SAT picks one flight and one hotel: minimise price + p% × fee, subject to total ≤ budget and only known policies. |

See [ARCHITECTURE.md](ARCHITECTURE.md) for the data flow and the security model.

---

## Using the MCP server from Claude Desktop or MCP Inspector

```json
{"mcpServers": {"reversa": {"command": "/abs/path/to/.venv/bin/python", "args": ["/abs/path/to/reversa/mcp_server.py"]}}}
```

The stdio server shares `reversa.db` and `.reversa_secret` with the web app, so a model can quote and hold, and you
can watch its holds appear in the UI. There is **no approve tool**. To commit, a model needs a token that only the
web UI's `/api/approve` endpoint can mint, and only after a human types or says the exact amount.

---

## What is simulated, and known limitations

- **Supplier:** the flights and hotels in `reversa/catalog.py` are fictional, with fixed prices and policies. No airline,
  GDS, payment gateway or UPI is involved, and "commit" only updates our own ledger.
- **LLM:** intent extraction is rule-based (`speech.py`) so the demo is deterministic and works offline. A real model
  can drive the same tools over MCP. Either way, the model never authorises money: the safety engine and approval gate
  are plain code.
- **Speech:** the browser's built-in recogniser. The ₹40,000 misrecognition is *simulated* by sending the misheard
  transcript; the backend's handling of it is real.
- **Time:** every trip departs 10 days after "now". The exposure slider shows what the fees would be closer to
  departure. Approval tokens use real wall-clock time (120 s TTL).
- **Holds** don't expire automatically.
- **Single user:** no accounts or auth. The MCP stdio server and the web app share one SQLite file and one secret.
- **Token binding:** the token proves that the backend saw the user state the exact amount in this app. It is not
  tied to a voiceprint or device.
