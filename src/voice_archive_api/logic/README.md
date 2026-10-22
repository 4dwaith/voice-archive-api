# Pure logic

This package owns rules, validation, transformations, and implementation-independent
types. Code here accepts supplied values and returns results or validation errors.
It performs no database, HTTP, filesystem, or storage operations. Parsing and
validation libraries such as Pydantic, html5lib, and langcodes are permitted.

- `books/`: metadata normalization/validation and text length limits.
- `content_validation/`: canonical input schemas, HTML/text derivation, hierarchy,
  media-reference validation, and closed section/segment kinds. The guide, generated
  JSON Schema, and complete synthetic example stay alongside the module.
- `files.py`: file kinds and immutable prepared-asset snapshot contracts.

Services perform I/O and supply its results to these functions. In particular,
`referenced_file_ids()` identifies the files to load; services load/lock their
snapshots; `validate_assets()` checks the supplied mapping. The logic package
imports neither ORM models nor services. Persistence models can reuse its enums
and constants without introducing implementation dependencies into validation.

Pure tests live in `tests/logic/`; persisted-file integration tests live in
`tests/services/content/`. `tests/logic/test_dependencies.py` guards imports from
implementation layers. No directory is named `application/` or `domain/` in the
new structure.
