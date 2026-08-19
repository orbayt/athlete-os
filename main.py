from fastmcp import FastMCP

from athlete_os.tools.activities import activities, recent_activities
from athlete_os.tools.health import health_check
from athlete_os.tools.wellness import recent_wellness, wellness
from athlete_os.tools.training_state import training_state


mcp = FastMCP(
    "Athlete OS",
    instructions="Provides endurance training data and analysis tools.",
)

mcp.tool(health_check)
mcp.tool(recent_activities)
mcp.tool(activities)
mcp.tool(recent_wellness)
mcp.tool(wellness)
mcp.tool(training_state)

if __name__ == "__main__":
    mcp.run()
