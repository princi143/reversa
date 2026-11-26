"""Reversa MCP server (FastMCP) - the four booking tools.

    quote_booking   destination + budget -> priced options with cancellation exposure
    hold_booking    reserve one option; money moves free -> held (nothing charged)
    commit_booking  needs a signed approval token; the Financial Safety Engine runs first
    cancel_booking  holds are released free; a committed booking needs an approved fee

There is deliberately NO approve tool. Tokens are minted only by the web backend's
/api/approve endpoint after the human states the exact amount, so a model connected
over MCP can request bookings but cannot authorise spending.

The web app (app.py) talks to this same server object over an in-memory MCP session.
Run it standalone for an external MCP client (Claude Desktop, MCP Inspector):

    python mcp_server.py            # stdio transport
It shares reversa.db and the .reversa_secret file with the web app.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from reversa.service import BookingService

INSTRUCTIONS = (
    "You are Reversa, a travel booking assistant. Use quote_booking to find options and hold_booking to "
    "reserve the one the user picks. You cannot approve payments: commit_booking and paid cancellations "
    "require an approval_token that only the user can create in the Reversa app by stating the exact amount. "
    "Never claim a booking is confirmed unless commit_booking returned ok=true. All amounts are integer paise.")


def build_server(svc: BookingService) -> FastMCP:
    mcp = FastMCP("reversa", instructions=INSTRUCTIONS)

    @mcp.tool()
    def quote_booking(destination: str, budget_rupees: int, nights: int = 3) -> dict:
        """Get flight and hotel options for a destination ('goa' or 'manali') within a budget in rupees.
        Each option includes price and cancellation fee/refundable amount in paise."""
        return svc.quote(destination, int(budget_rupees) * 100, int(nights))

    @mcp.tool()
    def hold_booking(trip_id: str, option_id: str) -> dict:
        """Place a temporary hold on one option from quote_booking. Nothing is charged; the amount is
        earmarked in the ledger. Returns the transaction and the safety checks still outstanding."""
        return svc.hold(trip_id, option_id)

    @mcp.tool()
    def commit_booking(transaction_id: str, approval_token: str) -> dict:
        """Commit a held booking. Requires the user's signed approval token for this exact transaction,
        amount, supplier and action. Rejected if any of the five safety checks fails."""
        return svc.commit(transaction_id, approval_token)

    @mcp.tool()
    def cancel_booking(transaction_id: str, approval_token: str = "") -> dict:
        """Cancel a booking. A hold is released at no cost. A committed booking costs its cancellation
        fee and needs the user's signed approval of that fee; the refund stays pending until it settles."""
        return svc.cancel(transaction_id, approval_token or None)

    return mcp


if __name__ == "__main__":
    build_server(BookingService()).run()   # stdio
