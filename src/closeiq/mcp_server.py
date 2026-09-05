from mcp.server import MCPServer

from closeiq.api import get_close_summary


mcp = MCPServer("CloseIQ")


@mcp.tool()
def close_summary() -> dict[str, int]:
    """Return read-only CloseIQ exception counts by workflow status."""
    return get_close_summary()