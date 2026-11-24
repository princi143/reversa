"""Model-facing MCP server for Reversa.

It talks to the host over HTTP and has no database access and no signing secret.
Every tool either reads, or *requests* an action. The traveller's spoken
"confirm <amount>" goes from the browser straight to the host, so a model (or text
injected into it) has no tool that can spend money.

    python mcp_server.py            # stdio, for Claude Desktop / any MCP client
    python mcp_server.py --http     # streamable HTTP
Start the host first:  uvicorn app:app
"""
import json
import os
import sys
import urllib.request

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

HOST = os.environ.get("REVERSA_HOST", "http://localhost:8000")
mcp = _Server("reversa", instructions=(
    "You are a voice travel assistant. Speak each tool's 'say' text to the traveller exactly; never do "
    "money arithmetic yourself. You cannot confirm anything: when a reply ends with \"say: confirm <amount>\", "
    "the traveller must say it aloud to the app. Never claim something is booked or cancelled until "
    "get_trip shows it."))


def _call(path: str, body: dict | None = None):
    req = urllib.request.Request(HOST + path, data=json.dumps(body or {}).encode() if body is not None else None,
                                 headers={"content-type": "application/json"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


@mcp.tool()
def plan_trip(destination: str, nights: int, budget_rupees: int, people: int = 2) -> dict:
    """Quote a trip and place free holds (no charge). destination: goa/beach or manali/hills."""
    return _call("/api/agent/plan_trip", {"destination": destination, "nights": nights,
                                          "budget_rupees": budget_rupees, "people": people})


@mcp.tool()
def request_booking() -> dict:
    """Ask to commit the held bookings. Returns a read-back the traveller must confirm aloud."""
    return _call("/api/agent/request_booking", {})


@mcp.tool()
def request_replan(destination: str) -> dict:
    """Ask to switch destination. Returns the minimal-fee diff and its price for spoken confirmation."""
    return _call("/api/agent/request_replan", {"destination": destination})


@mcp.tool()
def request_cancel(category: str) -> dict:
    """Ask to cancel one booking: 'flight', 'hotel' or 'activity'. Returns the fee for confirmation."""
    return _call("/api/agent/request_cancel", {"category": category})


@mcp.tool()
def get_trip() -> dict:
    """Current bookings, the five ledger balances (paise), exposure now and how it will rise."""
    s = _call("/api/state")
    return {k: s[k] for k in ("trip", "items", "balances", "exposure", "forecast", "hours_left", "pending")}


@mcp.tool()
def reconcile() -> dict:
    """Replay the event log and prove all postings sum to zero."""
    return _call("/api/state")["reconcile"]


if __name__ == "__main__":
    mcp.run("streamable-http" if "--http" in sys.argv else "stdio")
