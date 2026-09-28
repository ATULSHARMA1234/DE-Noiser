from pathlib import Path
from typing import Any

import lancedb
import numpy as np
import pyarrow as pa

from denoiser.config import settings
from denoiser.logging import get_logger

logger = get_logger(__name__)

# Define the schema for the embeddings table
# We use pyarrow schema for exact control
#
# `tenant_id` is stored as a string rather than an int because it is only ever
# used as an equality filter, and the rest of the storage layer (ClickHouse,
# object keys) already carries the tenant as a string.
schema = pa.schema([
    pa.field("id", pa.string()),
    pa.field("tenant_id", pa.string()),
    pa.field("vector", pa.list_(pa.float32(), settings.embedding_dimension)),
    pa.field("template", pa.string()),
    pa.field("source", pa.string()),
    pa.field("timestamp", pa.int64()),
])

#: Rows written before the table was tenant-scoped carry this in `tenant_id`.
#: They are not attributable to any customer, so they match no tenant's filter
#: and are simply never returned — quarantined rather than deleted, so an
#: operator can inspect or purge them deliberately.
UNKNOWN_TENANT = ""


class VectorStore:
    def __init__(self, uri: str = "data/lancedb"):
        self.uri = uri
        self.db = None
        self.table_name = "log_embeddings"
        self._init_db()

    def _init_db(self):
        try:
            # Ensure parent dir exists
            Path(self.uri).parent.mkdir(parents=True, exist_ok=True)
            self.db = lancedb.connect(self.uri)

            if self.table_name not in self.db.table_names():
                logger.info(f"Creating new LanceDB table '{self.table_name}' at {self.uri}")
                self.db.create_table(self.table_name, schema=schema)
            else:
                self._ensure_tenant_column()
        except Exception as e:
            logger.error(f"Failed to initialize LanceDB: {e}")

    def _ensure_tenant_column(self):
        """Backfill `tenant_id` onto a table created before tenant scoping.

        The alternative — dropping and rebuilding the table — is tempting because
        embeddings are derived data, but it would silently discard whatever an
        operator had accumulated. Adding the column keeps the rows and lets the
        `UNKNOWN_TENANT` filter hide them instead.
        """
        table = self.db.open_table(self.table_name)
        if "tenant_id" in table.schema.names:
            return
        logger.warning(
            "LanceDB table '%s' predates tenant scoping; adding tenant_id. Existing "
            "rows are marked unattributed and will not be returned by any search.",
            self.table_name,
        )
        table.add_columns({"tenant_id": "''"})

    def add_embeddings(
        self,
        ids: list[str],
        vectors: list[list[float]],
        templates: list[str],
        sources: list[str],
        timestamps: list[int],
        tenant_id: Any = None,
    ):
        """Persist new embeddings to the vector database.

        `tenant_id` is required in practice: a row written without one is
        unattributable and no search will ever return it, so we refuse rather
        than accept writes that quietly vanish.
        """
        if not self.db:
            return False

        tenant_key = "" if tenant_id is None else str(tenant_id).strip()
        if not tenant_key:
            logger.error("Refusing to persist embeddings without a tenant_id.")
            return False

        try:
            table = self.db.open_table(self.table_name)
            vector_rows = np.asarray(vectors, dtype=np.float32).tolist()

            # Format data as a list of dictionaries matching the schema
            data = [
                {
                    "id": ids[i],
                    "tenant_id": tenant_key,
                    "vector": vector_rows[i],
                    "template": templates[i],
                    "source": sources[i] if i < len(sources) else "unknown",
                    "timestamp": timestamps[i] if i < len(timestamps) else 0
                }
                for i in range(min(len(ids), len(vector_rows), len(templates)))
            ]

            if not data:
                return True

            table.add(data)
            logger.info(f"Persisted {len(ids)} embeddings to LanceDB for tenant {tenant_key}.")
            return True
        except Exception as e:
            logger.error(f"Failed to add embeddings to LanceDB: {e}")
            return False

    def search(self, query_vector: list[float], tenant_id: Any, limit: int = 10) -> list[dict[str, Any]]:
        """Search one tenant's embeddings for semantically similar logs.

        `tenant_id` is positional and has no default so that a caller cannot
        omit it and silently receive every customer's log templates — the
        templates themselves are the sensitive part, since they carry table
        names, endpoints and internal hostnames.
        """
        if not self.db:
            return []

        tenant_key = "" if tenant_id is None else str(tenant_id).strip()
        if not tenant_key:
            return []

        try:
            table = self.db.open_table(self.table_name)
            # Escaped for the SQL-ish filter dialect; tenant ids are numeric in
            # practice, but the store accepts a string and must not be a way to
            # smuggle a predicate.
            escaped = tenant_key.replace("'", "''")
            results = (
                table.search(query_vector)
                .where(f"tenant_id = '{escaped}'")
                .limit(limit)
                .to_list()
            )
            return results
        except Exception as e:
            logger.error(f"Failed to search LanceDB: {e}")
            return []

    def delete_tenant(self, tenant_id: Any) -> int:
        """Remove every embedding belonging to a tenant. Returns rows deleted.

        Used by tenant offboarding: without it, a customer's log templates
        outlive their account in a store nothing else knows how to clean.
        """
        if not self.db:
            return 0

        tenant_key = "" if tenant_id is None else str(tenant_id).strip()
        if not tenant_key:
            return 0

        try:
            table = self.db.open_table(self.table_name)
            escaped = tenant_key.replace("'", "''")
            before = table.count_rows()
            table.delete(f"tenant_id = '{escaped}'")
            return before - table.count_rows()
        except Exception as e:
            logger.error(f"Failed to delete tenant embeddings from LanceDB: {e}")
            return 0
