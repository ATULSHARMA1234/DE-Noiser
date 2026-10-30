# Log query language (LQL)

The search box in Explore, `POST /v1/logs/query`, the Explore histogram, saved
queries and monitors all use the same language. It is parsed by
`src/denoiser/query/parser.py`, which compiles it to ClickHouse SQL (every value
bound as a parameter) and, in development, evaluates it in memory against log
files. Both paths are tested against the same cases, the SQL one on a real
ClickHouse.

## Terms

| You write | Matches |
|---|---|
| `timeout` | the word anywhere in the record (message or any field) |
| `"connection reset"` | the exact phrase |
| `level:ERROR` | field `level` equals `ERROR`. Case-insensitive |
| `status:500` | 500 as a number or a string: `500`, `500.0` and `"500"` all match |
| `status:>=500`, `>`, `<`, `<=` | numeric comparison; combine two for a range: `status:>=500 status:<600` |
| `path:/api/*`, `pay*` | `*` is a wildcard, in a field value or free text |
| `trace_id:*` | the field is present and not empty |
| `user.id:42` | a nested field, `{"user": {"id": 42}}`, or a literal `"user.id"` key |
| `msg:"say \"hi\""` | quoted values keep spaces; `\"` is a literal quote |

`source`, `level` and `message` are columns; any other field name is looked up
in the record's JSON.

`%` and `_` are ordinary characters, so `50%` finds "50%". Parentheses inside a
word are part of it when they balance, so `getUser()` is a search term.

## Combining

| You write | Means |
|---|---|
| `a b` or `a AND b` | both |
| `a OR b` | either |
| `NOT a` or `-a` | not |
| `(a OR b) c` | grouping |

`NOT` binds tightest, then `AND`, then `OR`, so `a OR b AND c` is
`a OR (b AND c)`. Operators are upper-case only: `and`, `or` and `not` in lower
case are searched for as words. A dash negates only directly before a word,
quote or parenthesis: `-5` and `a - b` are searches for the dash itself.

## Errors and limits

A malformed query is refused with a message naming the position (`400` from the
API, `422` when saving a monitor or saved query). It is never quietly
reinterpreted. Examples: an unclosed quote or parenthesis, an operator with
nothing on one side, `level:` with no value, or `status:>abc`.

A query may be at most 8,192 characters, 256 terms, and 32 levels of
parentheses or `NOT`.
