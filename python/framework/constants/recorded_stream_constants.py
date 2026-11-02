"""
FiniexTestingIDE - Recorded Stream Constants
The routes that serve every stream a unit of a run records over time (#580) — below a run's report
routes, shared by the routes that mount them and the test that holds every route below a run to the
store's declaration.
"""

# A unit's timeline: each recorded stream with its record count and the time of its first and
# latest record — made to be asked every second or two
UNIT_RECORDED_STREAMS_ROUTE = 'units/{unit_name}/recorded-streams'

# A unit's recorded streams within a time window, a page at a time
UNIT_RECORDED_STREAMS_WINDOW_ROUTE = 'units/{unit_name}/recorded-streams/window'

# Both, as the store's declaration claims them: every stream that declares a recorded-stream kind
# is served by them
RECORDED_STREAM_ROUTES = (UNIT_RECORDED_STREAMS_ROUTE, UNIT_RECORDED_STREAMS_WINDOW_ROUTE)
