from fastmcp import FastMCP

from athlete_os.tools.activities import activities, recent_activities
from athlete_os.tools.health import health_check
from athlete_os.tools.recovery_checkin import record_recovery_checkin
from athlete_os.tools.training_context import training_context
from athlete_os.tools.training_state import training_state
from athlete_os.tools.wellness import recent_wellness, wellness


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
mcp.tool(training_context)
mcp.tool(record_recovery_checkin)

if __name__ == "__main__":
    mcp.run()
