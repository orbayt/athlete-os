from fastmcp import FastMCP

from athlete_os.tools.health import health_check

mcp = FastMCP(
    "Athlete OS",
    instructions="Provides endurance training data and analysis tools.",
)

mcp.tool(health_check)


if __name__ == "__main__":
    mcp.run()