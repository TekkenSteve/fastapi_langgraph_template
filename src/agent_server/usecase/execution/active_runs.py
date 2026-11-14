"""Global registry of in-flight asyncio tasks for graph executions.

Defined in a dependency-free module so that any layer (API routes, broker
managers, streaming service) can import it without circular dependencies.
"""

import asyncio

active_runs: dict[str, asyncio.Task[None]] = {}

# Explicit API cancellations are marked so worker shutdown remains recoverable.
explicit_run_cancellations: set[str] = set()

# Runs currently inside execute_run in this process. A worker lists a run in
# active_runs before it takes the lease, so that one cannot tell whether
# execution will happen; execute_run joins this set before its first await and
# leaves it in its finally, so a listed run always reaches the code that
# reports it.
executing_runs: set[str] = set()
