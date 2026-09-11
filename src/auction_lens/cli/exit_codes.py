"""What this program tells the shell when it stops.

Two numbers, written down once. A scheduled run is read by Task Scheduler and
by run-daily.cmd, neither of which can see the report, so the exit code is the
whole of what they know. Keeping both here means the meaning of "2" cannot
drift apart between the command that returns it and the wrapper that reads it.
"""

from __future__ import annotations

SUCCESS = 0

# Everything the operator can fix themselves: a missing file, an unset
# variable, a flag combination that cannot mean anything. Deliberately not 1,
# so a crash and a refusal never look the same to a scheduler.
OPERATOR_ERROR = 2
