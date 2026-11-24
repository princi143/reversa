# Reversa — prototype

**Make MCP Matter · Track 01: No Screens.** Book at the speed of speech. Undo at the price of truth.

The LLM talks; deterministic code guards the money. Every booking is held before it is committed, every
undo has a spoken price, and every paisa sits in exactly one of five ledger states.

## Run it in VS Code

1. Open this folder in VS Code (`File → Open Folder…`).
2. Open a terminal (`` Ctrl+` ``) and run:

   ```bash
   python -m venv .venv
   # Windows:      .venv\Scripts\activate
   # macOS/Linux:  source .venv/bin/activate
   pip install -r requirements.txt
   pytest -q                     # 21 tests
   uvicorn app:app --reload      # then open http://localhost:8000
   ```

   Or press **F5** and pick "Reversa host" (uses `.vscode/launch.json`).
3. Use **Chrome or Edge** for the microphone. Typing and the demo buttons work in any browser.

Needs Python 3.10+. State is stored in `reversa.db` in this folder; the **Reset** button clears it.

## The five-turn demo (slide 4)

| Turn | Say | What happens |
|---|---|---|
| 1 | "Goa, 3 nights, ₹40,000, two people." | Holds flight + hotel + activity (₹25,800). Exposure ₹0. |
| 2 | "Book it." then "confirm 25800" | Read-back, token minted, ladder commits cheapest-to-undo first. Exposure ₹2,400. |
| 3 | "Actually hills, not beach." then "confirm 1200" | CP-SAT moves the hotel to its sister property and cancels only the flight: ₹1,200 instead of ₹2,400. |
| 4 | "Cancel the activity." then "confirm 200" | ₹1,800 goes to refund-pending and stays out of free cash. |
| 5 | "Reconcile." | Replays the SQLite event log; postings sum to ₹0. |

Also worth showing: **Say a wrong number** (nothing changes), **Clock +3 days** (exposure climbs the curve),
**Make next flight commit time out** before a confirm (reverse saga), and **Bank settles refunds**.

## Map from deck to code

| Deck | Where |
|---|---|
| Hold before commit, priced commit ladder, reverse sagas | `reversa/engine.py` |
| Time-varying reversibility curve E(t) | `reversa/catalog.py` (`cancel_fee`), `engine.forecast()` |
| Minimal-regret CP-SAT diff, strict cash fence | `reversa/solver.py` |
| Zero-sum ledger, five states, `reconcile()`, idempotency keys | `reversa/ledger.py` |
| Numeric read-back and signed single-use voice token | `engine.confirm()` / `engine.execute()` |
| Speech → intent, number words ("forty thousand") | `reversa/speech.py` |
| FastAPI policy gate + live inspector UI | `app.py`, `static/index.html` |
| MCP server (request-only tools) | `mcp_server.py` |
| Every claim above as a test | `tests/test_reversa.py` |

## Connecting a real model (optional)

Start the host, then run `python mcp_server.py` from an MCP client (Claude Desktop config below). The server
reaches the host over HTTP only: it has no database access, no signing secret, and no confirm tool.

```json
{"mcpServers": {"reversa": {"command": "python", "args": ["/abs/path/to/mcp_server.py"]}}}
```

## What is simulated

- **Suppliers**: flights, hotels and activities come from `reversa/catalog.py`. No real airline or hotel is called.
- **The LLM**: intent parsing in the web UI is rule-based (`speech.py`), so the demo is deterministic and
  offline. A real model plugs in through `mcp_server.py`.
- **Voice**: the browser's built-in speech recognition and speech synthesis.
- **Time and refunds**: the clock and bank settlement are the buttons under "Demo controls".
- **UI**: one static HTML page, not the React + Vite + Tailwind named on slide 5.
- **Token**: HMAC-signed and bound to the read-back, checked inside the host process. It is not tied to a
  voiceprint; "unforgeable" here means a model cannot mint or replay one.
