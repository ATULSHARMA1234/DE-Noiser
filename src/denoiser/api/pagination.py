"""
Shared pagination bounds.

Page-size limits were being decided per endpoint, which produced three
different behaviours across the API: some routes declared a Pydantic ``le=``,
some clamped by hand, and some accepted whatever arrived. The gaps mattered in
two directions.

A missing upper bound lets one request ask for every row in a table. A missing
*lower* bound is worse and less obvious: both SQLite and PostgreSQL treat
``LIMIT -1`` as "no limit", so ``?limit=-1`` on an unbounded endpoint is an
unpaginated full-table scan that looks like an ordinary request.

Importing these constants keeps the answer in one place.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path, Query

#: Largest page any list endpoint will serve.
MAX_PAGE_SIZE = 1000

#: Largest value a signed 64-bit integer column can hold. A path parameter
#: above this overflows in the database driver and surfaces as a 500 —
#: ``GET /incidents/99999999999999999999`` was an internal server error rather
#: than the "no such id" it plainly is. Bounding the parameter turns it back
#: into a 422 that never reaches the database.
MAX_DB_ID = 9223372036854775807

#: An integer resource identifier in a URL path.
ResourceId = Annotated[int, Path(ge=1, le=MAX_DB_ID)]

#: Default page size where a route does not state its own.
DEFAULT_PAGE_SIZE = 100


def limit_param(default: int = DEFAULT_PAGE_SIZE, maximum: int = MAX_PAGE_SIZE) -> Query:
    """A bounded ``limit`` query parameter."""
    return Query(default, ge=1, le=maximum, description="Maximum rows to return")


def offset_param(default: int = 0) -> Query:
    """A non-negative ``offset``/``skip`` query parameter."""
    return Query(default, ge=0, description="Rows to skip before returning results")
