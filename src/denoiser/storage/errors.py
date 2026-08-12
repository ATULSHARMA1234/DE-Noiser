"""The one thing a store must never do: make its own absence look like an answer.

Every store in this package used to signal failure by returning a value shaped
like success — ``[]``, ``0.0``, ``False``, ``{"source": [], "level": []}``. Six
conventions across four modules, three of them inside a single class.

For a read that only renders, that is merely misleading: an outage draws an
empty chart. For a read whose result is *written back*, it is corrupting.
``aggregate_metric`` returned ``0.0`` when ClickHouse was unreachable and the
metric worker stored it, once a minute, as a genuine observation of zero. The
billing pass did the same at the tenant level, committing a day of zero usage
for every customer.

The discipline already existed elsewhere in the codebase and simply had not
reached the stores:

    evaluate_slos   — "skip rather than record a perfect score earned by an
                      absence of evidence"
    _consumer_lag   — "an unknown lag must not be reported as zero, which would
                      read as 'fully caught up'"

`StoreUnavailable` is that rule made reusable. A store raises it when it cannot
answer; a caller that is allowed to degrade catches it *explicitly*, at the one
place where degrading is a decision someone made rather than a value that leaked
through.
"""

from __future__ import annotations


class StoreUnavailable(RuntimeError):
    """A backing store could not be reached, so no answer exists.

    Distinct from an empty answer. Catch this only where "we do not know" has a
    defined behaviour — skipping a datapoint, returning 503 — never to turn it
    back into a zero.
    """

    def __init__(self, store: str, detail: str | None = None) -> None:
        self.store = store
        self.detail = detail
        super().__init__(f"{store} is unavailable" + (f": {detail}" if detail else ""))
