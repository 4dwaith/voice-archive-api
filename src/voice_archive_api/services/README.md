# Services

Services are grouped by capability: `books/`, `content/`, and `files/`. Each package
keeps its service operations and input/result schemas together. Services own
database access, transactions, file preparation, and coordination with pure logic.
SQLAlchemy models live in `db/models/`; implementation-independent rules and types
live in `logic/`. HTTP routes and local commands call services directly.

`library/search/` groups passage retrieval: `dense.py` owns exact vector queries,
`lexical.py` owns full-text/trigram queries, `retrieval.py` coordinates search
channels and source/evidence reads, and `expansion.py` loads candidate text,
summaries and neighboring context. Pure ranking rules live in `logic/search/`.
The library's `service.py` orchestrates question answering and `provider.py`
owns model calls. Tests mirror search responsibilities under `tests/logic/search/`,
`tests/services/library/search/` and `tests/database/search/` for PostgreSQL-specific
dictionary/index behavior.

`services/embeddings/` groups batch embedding operations. `submit_batches.py` owns
queue admission, input selection and provider submission; `collect_results.py`
polls provider jobs, reconciles interrupted submissions and publishes vectors.
`service.py` coordinates the CLI submission and periodic maintenance entry points.
The operation-independent queue remains in `services/batch_queue.py`.

`services/content/service.py` owns canonical publication, manifest, and purge.
It loads/locks prepared-file snapshots through the file service and passes those
values to `logic/content_validation/` for validation. Pure logic has no session,
file lookup callback, or knowledge of database/file storage implementations.

`services/book_cleanup.py` shares content/file deletion between content purge and
whole-book deletion. It removes dependent content and owned file records, queues
physical cleanup, and leaves the book lock and transaction with the caller.

File preparation strategies and results are in `services/files/types.py`; shared
file kinds and immutable asset snapshots are in `logic/files.py`. File delivery
and storage primitives remain in `file_handling/`.
