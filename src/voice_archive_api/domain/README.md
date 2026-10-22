# Domain packages

Related models, rules, and database-aware services live together by capability:

- `books/`: book metadata, identity rules, and book lifecycle services.
- `content/`: section and segment persistence models and their enums.
- `files/`: file preparation, ownership, lookup, delivery strategies, and cleanup.

HTTP handlers and application capabilities use these services for database access.
Broader workflows live in `application/`; storage and streaming mechanics live in
`file_handling/`. Database constraint and service integration tests live in
`tests/database/`.
