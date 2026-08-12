"""repair incident titles stored as Postgres array literals

`failure_domain` comes back from the model as either a string or a list — the
prompt asks for the failed "component(s)". The list went straight into
`incidents.title` and `incidents.domain`, which are `String` columns, so psycopg
rendered it as a Postgres array literal and the incident list displayed
`{"Memory Subsystem","Disk I/O Subsystem"}` as an incident's name.

The write path now normalises to a comma-joined string. This repairs the rows
written before it did, so the existing incident list stops showing the literal.

Matching is deliberately narrow: `{"` … `"}`, which is what the array rendering
always produces and what a model-written domain never does. A title that merely
contains a brace is left alone.

Revision ID: d0a6b41e73c5
Revises: c9f207b3e814
Create Date: 2026-08-04
"""

import re

import sqlalchemy as sa
from alembic import op

revision = "d0a6b41e73c5"
down_revision = "c9f207b3e814"
branch_labels = None
depends_on = None


ARRAY_LITERAL = re.compile(r'^\{".*"\}$', re.DOTALL)


def _unpack(literal: str) -> str:
    """`{"a","b"}` -> `a, b`. Returns the input unchanged if it does not match."""
    if not literal or not ARRAY_LITERAL.match(literal):
        return literal
    inner = literal[1:-1]
    # Elements are quoted; a quoted element may itself contain an escaped quote.
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', inner)
    cleaned = [p.replace('\\"', '"').replace("\\\\", "\\").strip() for p in parts]
    joined = ", ".join(p for p in cleaned if p)
    return joined or literal


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, title, domain FROM incidents "
            "WHERE title LIKE '{\"%' OR domain LIKE '{\"%'"
        )
    ).fetchall()

    for row in rows:
        title, domain = _unpack(row.title or ""), _unpack(row.domain or "")
        if title == (row.title or "") and domain == (row.domain or ""):
            continue
        bind.execute(
            sa.text("UPDATE incidents SET title = :t, domain = :d WHERE id = :i"),
            {"t": title, "d": domain, "i": row.id},
        )


def downgrade() -> None:
    # The original literal is not recoverable from the joined form, and it was
    # never a value anyone wanted. Nothing to undo.
    pass
