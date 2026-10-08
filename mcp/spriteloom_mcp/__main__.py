"""Run Spriteloom's MCP tools over stdio."""

from .tools import mcp


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
