# Athlete OS Development Guidelines

## Architecture

- `athlete_os/services/` contains integrations with external systems such as Intervals.icu.
- `athlete_os/tools/` contains MCP-facing Athlete OS capabilities.
- External provider data must be normalized before being exposed to MCP tools.
- MCP tools should return provider-agnostic Athlete OS schemas.
- Do not expose raw Intervals.icu payloads from MCP tools.

## Design Principles

- Keep functions small and composable.
- Prefer simple deterministic transformations over hidden heuristics.
- Do not invent health, readiness, recovery, or performance scores unless the scoring model is explicitly defined.
- Preserve missing data as `None` rather than inferring values.
- Include data coverage or measureme# Athlete OS Development Guidelines

## Architecture

- `athlete_os/services/` contains integrations with external systems such as Intervals.icu.
- `athlete_os/tools/` contains MCP-facing Athlete OS capabilities.
- External provider data must be normalized before being exposed to MCP tools.
- MCP tools should return provider-agnostic Athlete OS schemas.
- Do not expose raw Intervals.icu payloads from MCP tools.

## Design Principles

- Keep functions small and composable.
- Prefer simple deterministic transformations over hidden heuristics.
- Do not invent health, readiness, recovery, or performance scores unless the scoring model is explicitly defined.
- Preserve missing data as `None` rather than inferring values.
- Include data coverage or measurement dates when stale or missing data could affect interpretation.
- Separate data acquisition, normalization, aggregation, and interpretation.

## Training State

- Activity totals should preserve activity-type breakdowns.
- Recovery metrics should include the date of the measurement when the latest value may not be from today.
- `current_state` represents the latest wellness state.
- `latest_recovery` represents the latest available non-null recovery measurements.
- CTL, ATL, ramp rate, and related provider metrics may be exposed, but Athlete OS interpretation should remain separate.

## Testing

- Add or update tests when changing Athlete OS behavior.
- Test missing and empty data cases.
- Run:

  `uv run python -m unittest discover -s tests -v`

- After adding or modifying MCP tools, verify discovery with:

  `uv run fastmcp list main.py`

## Scope Discipline

- Do not refactor unrelated code unless necessary.
- Do not add dependencies unless required.
- Do not change external service behavior without a clear reason.
- Prefer incremental changes that keep the MCP server working.nt dates when stale or missing data could affect interpretation.
- Separate data acquisition, normalization, aggregation, and interpretation.

## Training State

- Activity totals should preserve activity-type breakdowns.
- Recovery metrics should include the date of the measurement when the latest value may not be from today.
- `current_state` represents the latest wellness state.
- `latest_recovery` represents the latest available non-null recovery measurements.
- CTL, ATL, ramp rate, and related provider metrics may be exposed, but Athlete OS interpretation should remain separate.

## Testing

- Add or update tests when changing Athlete OS behavior.
- Test missing and empty data cases.
- Run:

  `uv run python -m unittest discover -s tests -v`

- After adding or modifying MCP tools, verify discovery with:

  `uv run fastmcp list main.py`

## Scope Discipline

- Do not refactor unrelated code unless necessary.
- Do not add dependencies unless required.
- Do not change external service behavior without a clear reason.
- Prefer incremental changes that keep the MCP server working.