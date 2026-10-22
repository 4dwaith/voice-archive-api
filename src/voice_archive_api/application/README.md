# Application capabilities

Each subpackage implements a cohesive application capability using the existing
domain models and services. HTTP routes can call these modules without owning
the underlying rules. Keep each capability's README and supporting documentation
with its implementation, and mirror its name under `tests/application/`.

- [Content validation](content_validation/README.md): canonical prepared-content
  schema, limited HTML and derived text, hierarchy/order checks, and prepared-file
  validation. Its versioned JSON Schema and example are in the module's `docs/`.

Book, content, and file persistence packages remain in their current locations;
the domain-layer reorganization is a separate change. Database constraint and
migration tests stay in `tests/database/`; HTTP and feature tests keep their
existing locations.
