# homesolar

A single process that polls local solar inverters, stores their telemetry in SQLite, and serves a
dashboard, API, and daily email reports. This file records the project's domain language so refactors
and reviews stay consistent.

## Language

**Produced energy**:
The kWh an inverter generated over a window. Computed per local day as the daily energy counter for
that day when a reading carried one, otherwise the sum of that day's normal-confidence energy
intervals, then summed across every local day the window touches.
_Avoid_: generated kwh, output, yield, production.

**Daily energy counter**:
The inverter-reported cumulative kWh for the current local day (`energy_today_kwh`), which resets at
local midnight. Trusted ahead of energy intervals when present.
_Avoid_: today kwh, daily total.

**Energy interval**:
A computed kWh delta between two consecutive readings, tagged with a source counter and a confidence;
only `normal`-confidence intervals count toward produced energy.
_Avoid_: delta, slice, segment.

**Archive**:
The read side over stored inverter telemetry — latest and windowed readings, components, energy
intervals, poll events, and alarms — plus the produced-energy rule computed over them. Owns its own
session; returns value objects, never live ORM rows. Distinct from the writer in the collector, which
takes an injected session.
_Avoid_: repository, store, dao, queries.

**14-day median**:
The median of daily produced energy over the trailing 14 local days, excluding the current day. Shown
beside today's produced energy as neutral context, never as a good/bad verdict. Scoped to whichever
inverter (or the whole house) the viewer has selected.
_Avoid_: average, baseline, target, expected yield, normal.
