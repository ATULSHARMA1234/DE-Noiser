# Third-Party Licenses

Every third-party component redistributed in a SemanticOS deployment, with its
license. Procurement asks for this by name, and until now the project satisfied
no attribution requirement for the BSD/MIT/Apache components it ships inside its
Docker image.

Regenerate after any dependency change:

```bash
uv run python scripts/generate_licenses.py > THIRD_PARTY_LICENSES.md
```

## Summary

**No GPL, AGPL, SSPL, or non-commercial license appears in the Python tree.**
The dependency set is permissive throughout and imposes no copyleft obligation
on SemanticOS itself.

Three components need a stated position rather than a licence line, and they are
below the table.

## Positions

### psycopg2-binary — LGPL with exceptions

Dynamically linked and unmodified. The LGPL's obligations attach to
distributing a modified library or statically linking it, neither of which this
project does, and running it as part of a hosted or on-premise service is not
distribution of the library. **No obligation triggered.** This would need
revisiting only if SemanticOS ever shipped a statically linked binary
distribution.

### certifi, pathspec, fqdn, tqdm — MPL-2.0

File-level copyleft. The obligation attaches to modified *files* of the covered
work; unmodified use imposes nothing on surrounding code. **No obligation
triggered.** Revisit only if one of these is patched in place rather than
upgraded.

### Redpanda — BSL 1.1 (a container image, not a Python dependency)

**No longer shipped by default.** The default broker is `apache/kafka:3.9.0`
(Apache-2.0, no restriction). Redpanda remains available as an explicit opt-in
via `docker-compose.redpanda.yml`, so nothing is lost for a deployer who prefers
it and knows what they are choosing.

The Business Source License forbids offering the software **as a managed service
to third parties**. While Redpanda was the default, the position was "acceptable,
because SemanticOS is distributed on-premise" — true, but it made a licensing
decision on the deployer's behalf, and it would have expired silently the moment
a hosted tier existed. Choosing the override is now that deployer's decision to
make, not an inherited default.

### Other container images

| Image | License |
|---|---|
| `postgres:15-alpine` | PostgreSQL License (permissive) |
| `clickhouse/clickhouse-server` | Apache-2.0 |
| `apache/kafka` | Apache-2.0 |
| `redis:7-alpine` | RSALv2 / SSPLv1 for Redis 7.4+ — see note |
| `minio/minio` | AGPL-3.0 — see note |
| `caddy:2-alpine` | Apache-2.0 |

**Redis** relicensed from BSD to RSALv2/SSPLv1 at 7.4. Both forbid offering
Redis itself as a managed service; neither restricts using it as a cache inside
a product. Same expiry condition as Redpanda — if a hosted tier appears, pin
`redis:7.2-alpine` (still BSD) or move to Valkey.

**MinIO** is AGPL-3.0. It is used unmodified as a container and is not linked
into SemanticOS, so the AGPL's network-use clause attaches to MinIO, not to this
project. It is also optional — any S3-compatible endpoint works, and a
deployment on AWS S3 does not ship it at all. If a hosted tier appears, use the
cloud provider's object storage rather than bundling MinIO.

## Frontend

`web/package.json` resolves to Next.js, React, Tailwind, Recharts, ECharts and
their transitive dependencies — MIT or Apache-2.0 throughout. Regenerate with:

```bash
cd web && npx license-checker --summary
```

## Python dependencies

| Package | Version | License |
|---|---|---|
| aiokafka | 0.14.0 | see package metadata |
| alembic | 1.18.5 | see package metadata |
| amqp | 5.3.1 | BSD License |
| annotated-doc | 0.0.4 | see package metadata |
| annotated-types | 0.7.0 | MIT License |
| anyio | 4.13.0 | see package metadata |
| appnope | 0.1.4 | BSD License |
| APScheduler | 3.11.2 | MIT License |
| argon2-cffi | 25.1.0 | see package metadata |
| argon2-cffi-bindings | 25.1.0 | see package metadata |
| arrow | 1.4.0 | Apache Software License |
| ast_serialize | 0.3.0 | see package metadata |
| asttokens | 3.0.2 | Apache 2.0 |
| async-lru | 2.3.0 | MIT License |
| async-timeout | 5.0.1 | Apache Software License |
| attrs | 26.1.0 | see package metadata |
| babel | 2.18.0 | BSD License |
| bcrypt | 5.0.0 | Apache Software License |
| beautifulsoup4 | 4.15.0 | MIT License |
| billiard | 4.2.4 | BSD License |
| bleach | 6.4.0 | Apache Software License |
| boto3 | 1.43.6 | Apache-2.0 |
| botocore | 1.43.6 | Apache-2.0 |
| celery | 5.6.3 | BSD-3-Clause |
| certifi | 2026.4.22 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.0.0 | see package metadata |
| charset-normalizer | 3.4.7 | MIT |
| click | 8.3.3 | see package metadata |
| click-didyoumean | 0.3.1 | MIT License |
| click-plugins | 1.1.1.2 | BSD License |
| click-repl | 0.3.0 | MIT |
| clickhouse-connect | 1.0.1 | Apache Software License |
| comm | 0.2.3 | BSD License |
| contourpy | 1.3.3 | BSD License |
| coverage | 7.13.5 | Apache-2.0 |
| cryptography | 48.0.0 | see package metadata |
| cycler | 0.12.1 | BSD License |
| debugpy | 1.8.21 | MIT License |
| decorator | 5.3.1 | BSD-2-Clause |
| defusedxml | 0.7.1 | Python Software Foundation License |
| deprecation | 2.1.0 | Apache Software License |
| distro | 1.9.0 | Apache Software License |
| duckdb | 1.5.5 | MIT License |
| durationpy | 0.10 | MIT |
| ecdsa | 0.19.2 | MIT |
| executing | 2.2.1 | MIT License |
| fastapi | 0.136.1 | see package metadata |
| fastjsonschema | 2.22.1 | BSD License |
| filelock | 3.29.0 | MIT License |
| fonttools | 4.63.0 | MIT |
| fqdn | 1.5.1 | Mozilla Public License 2.0 (MPL 2.0) |
| fsspec | 2026.4.0 | see package metadata |
| h11 | 0.16.0 | MIT License |
| hdbscan | 0.8.42 | OSI Approved |
| hf-xet | 1.5.0 | Apache Software License |
| httpcore | 1.0.9 | BSD License |
| httpx | 0.28.1 | BSD License |
| huggingface_hub | 1.14.0 | Apache Software License |
| idna | 3.13 | see package metadata |
| iniconfig | 2.3.0 | see package metadata |
| ipykernel | 7.3.0 | see package metadata |
| ipython | 9.15.0 | see package metadata |
| ipython_pygments_lexers | 1.1.1 | BSD License |
| ipywidgets | 8.1.8 | BSD License |
| isoduration | 20.11.0 | ISC License (ISCL) |
| jedi | 0.20.0 | MIT License |
| Jinja2 | 3.1.6 | BSD License |
| jiter | 0.14.0 | see package metadata |
| jmespath | 1.1.0 | MIT License |
| joblib | 1.5.3 | see package metadata |
| json5 | 0.15.0 | Apache Software License |
| jsonpointer | 3.1.1 | BSD License |
| jsonschema | 4.26.0 | see package metadata |
| jsonschema-specifications | 2025.9.1 | see package metadata |
| jupyter | 1.1.1 | BSD License |
| jupyter-console | 6.6.3 | BSD License |
| jupyter-events | 0.12.1 | BSD License |
| jupyter-lsp | 2.3.1 | BSD License |
| jupyter_builder | 1.1.1 | BSD License |
| jupyter_client | 8.9.1 | BSD License |
| jupyter_core | 5.9.1 | see package metadata |
| jupyter_server | 2.20.0 | BSD License |
| jupyter_server_terminals | 0.5.4 | BSD License |
| jupyterlab | 4.6.2 | BSD License |
| jupyterlab_pygments | 0.3.0 | BSD License |
| jupyterlab_server | 2.28.0 | BSD License |
| jupyterlab_widgets | 3.0.16 | BSD License |
| kiwisolver | 1.5.0 | BSD License |
| kombu | 5.6.2 | BSD-3-Clause |
| kubernetes | 35.0.0 | Apache Software License |
| lance-namespace | 0.7.6 | Apache-2.0 |
| lance-namespace-urllib3-client | 0.7.6 | Apache-2.0 |
| lancedb | 0.30.2 | Apache Software License |
| lark | 1.3.1 | MIT License |
| librt | 0.10.0 | see package metadata |
| llvmlite | 0.48.0 | see package metadata |
| lxml | 6.1.1 | BSD-3-Clause |
| lz4 | 4.4.5 | BSD License |
| Mako | 1.3.12 | MIT License |
| markdown-it-py | 4.2.0 | MIT License |
| MarkupSafe | 3.0.3 | see package metadata |
| matplotlib | 3.11.1 | Python Software Foundation License |
| matplotlib-inline | 0.2.2 | see package metadata |
| mdurl | 0.1.2 | MIT License |
| mistune | 3.3.4 | BSD License |
| mpmath | 1.3.0 | BSD License |
| mypy | 2.0.0 | see package metadata |
| mypy_extensions | 1.1.0 | see package metadata |
| nbclient | 0.11.0 | BSD License |
| nbconvert | 7.17.1 | BSD License |
| nbformat | 5.10.4 | BSD License |
| nest-asyncio2 | 1.7.2 | BSD License |
| networkx | 3.6.1 | see package metadata |
| notebook | 7.6.1 | BSD License |
| notebook_shim | 0.2.4 | BSD License |
| numba | 0.66.0 | BSD License |
| numpy | 2.4.4 | see package metadata |
| oauthlib | 3.3.1 | BSD-3-Clause |
| openai | 2.36.0 | Apache Software License |
| orjson | 3.11.9 | Apache Software License |
| packaging | 26.2 | see package metadata |
| pandas | 3.0.3 | BSD License |
| pandocfilters | 1.5.1 | BSD License |
| parso | 0.8.7 | MIT License |
| passlib | 1.7.4 | BSD |
| pathspec | 1.1.1 | Mozilla Public License 2.0 (MPL 2.0) |
| patsy | 1.0.2 | BSD License |
| pexpect | 4.9.0 | ISC License (ISCL) |
| pillow | 12.3.0 | see package metadata |
| platformdirs | 4.11.0 | MIT License |
| pluggy | 1.6.0 | MIT License |
| polars | 1.40.1 | MIT License |
| polars-runtime-32 | 1.40.1 | MIT License |
| prometheus_client | 0.26.0 | see package metadata |
| prompt_toolkit | 3.0.52 | BSD License |
| psutil | 7.2.2 | BSD-3-Clause |
| psycopg2-binary | 2.9.12 | GNU Library or Lesser General Public License (LGPL) |
| ptyprocess | 0.7.0 | ISC License (ISCL) |
| pure_eval | 0.2.3 | MIT License |
| pyarrow | 24.0.0 | see package metadata |
| pyasn1 | 0.6.3 | BSD-2-Clause |
| pycparser | 3.0 | see package metadata |
| pydantic | 2.13.4 | see package metadata |
| pydantic-settings | 2.14.0 | MIT License |
| pydantic_core | 2.46.4 | see package metadata |
| Pygments | 2.20.0 | see package metadata |
| pynndescent | 0.6.0 | see package metadata |
| pyparsing | 3.3.2 | see package metadata |
| pytest | 9.0.3 | see package metadata |
| pytest-asyncio | 1.4.0 | see package metadata |
| pytest-cov | 7.1.0 | MIT License |
| python-dateutil | 2.9.0.post0 | BSD License |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| python-jose | 3.5.0 | MIT License |
| python-json-logger | 4.1.0 | see package metadata |
| python-multipart | 0.0.27 | Apache Software License |
| PyYAML | 6.0.3 | MIT License |
| pyzmq | 27.1.0 | BSD License |
| redis | 7.4.0 | MIT License |
| referencing | 0.37.0 | see package metadata |
| regex | 2026.4.4 | see package metadata |
| requests | 2.33.1 | Apache Software License |
| requests-oauthlib | 2.0.0 | BSD License |
| respx | 0.23.1 | BSD License |
| rfc3339-validator | 0.1.4 | MIT License |
| rfc3986-validator | 0.1.1 | MIT License |
| rfc3987-syntax | 1.1.0 | Apache Software License |
| rich | 15.0.0 | MIT License |
| rpds-py | 2026.6.3 | see package metadata |
| rsa | 4.9.1 | Apache Software License |
| ruff | 0.15.12 | see package metadata |
| rxlens | 0.1.0 | MIT |
| s3transfer | 0.17.0 | Apache Software License |
| safetensors | 0.7.0 | Apache Software License |
| scikit-learn | 1.8.0 | see package metadata |
| scipy | 1.17.1 | BSD License |
| seaborn | 0.13.2 | BSD License |
| semantic-log-denoiser | 2.0.0 | MIT |
| Send2Trash | 2.1.0 | see package metadata |
| sentence-transformers | 5.4.1 | Apache Software License |
| setuptools | 81.0.0 | see package metadata |
| shellingham | 1.5.4 | ISC License (ISCL) |
| signxml | 5.1.0 | Apache Software License |
| six | 1.17.0 | MIT License |
| sniffio | 1.3.1 | MIT License |
| soupsieve | 2.9.1 | MIT License |
| SQLAlchemy | 2.0.49 | MIT |
| stack-data | 0.6.3 | MIT License |
| starlette | 1.0.0 | see package metadata |
| statsmodels | 0.14.6 | BSD License |
| sympy | 1.14.0 | BSD License |
| terminado | 0.18.1 | BSD License |
| threadpoolctl | 3.6.0 | BSD License |
| tinycss2 | 1.5.1 | BSD License |
| tokenizers | 0.22.2 | Apache Software License |
| torch | 2.11.0 | BSD-3-Clause |
| tornado | 6.5.7 | Apache Software License |
| tqdm | 4.67.3 | MPL-2.0 AND MIT |
| traitlets | 5.15.1 | BSD License |
| transformers | 5.8.0 | Apache 2.0 License |
| typer | 0.25.1 | see package metadata |
| typing-inspection | 0.4.2 | see package metadata |
| typing_extensions | 4.15.0 | see package metadata |
| tzdata | 2026.2 | Apache-2.0 |
| tzlocal | 5.3.1 | MIT License |
| umap-learn | 0.5.12 | OSI Approved |
| uri-template | 1.3.0 | MIT License |
| urllib3 | 2.7.0 | see package metadata |
| uvicorn | 0.46.0 | see package metadata |
| vine | 5.1.0 | BSD License |
| wcwidth | 0.7.0 | see package metadata |
| webcolors | 25.10.0 | BSD License |
| webencodings | 0.5.1 | BSD License |
| websocket-client | 1.9.0 | Apache Software License |
| websockets | 16.0 | see package metadata |
| widgetsnbextension | 4.0.15 | BSD License |
| zstandard | 0.25.0 | see package metadata |
