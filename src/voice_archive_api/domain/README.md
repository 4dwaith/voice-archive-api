# Domain packages

Related models, rules, and database-aware services live together by capability:

- `books/`: book metadata, identity rules, and book lifecycle services.
- `content/`: section/segment models, batched persistence, content state, and purge.
- `files/`: file preparation, ownership, lookup, delivery strategies, and cleanup.

HTTP handlers call these services for database access and transactions.
`application/` holds pure logic, called by domain services with supplied data;
storage and streaming mechanics live in `file_handling/`. Database constraint
and service integration tests live in `tests/database/`.

`domain/content/service.py` owns canonical publication, manifest, and purge.
Publication reuses `application/content_validation/` for pure validation and the
file service for prepared-asset lookup/locking and ownership claims. The existing
package names remain in place; a broader folder reorganization is deferred.

`domain/book_cleanup.py` shares content/file deletion between content purge and
whole-book deletion. It removes dependent content and owned file records, queues
physical cleanup, and leaves the book lock and transaction with the caller.
