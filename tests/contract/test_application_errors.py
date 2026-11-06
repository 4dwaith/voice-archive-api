"""Keep concrete application failures registered and documented by their domain."""

from voice_archive_api.api.errors import APPLICATION_ERROR_STATUSES
from voice_archive_api.core.errors import ApplicationError


def application_errors():
    pending = list(ApplicationError.__subclasses__())
    seen = set()
    while pending:
        error = pending.pop()
        if error in seen:
            continue
        seen.add(error)
        pending.extend(error.__subclasses__())
        # Exclude errors injected by tests to exercise unknown-error handling.
        if error.__module__.startswith("voice_archive_api."):
            yield error


def resolve(document, schema):
    while "$ref" in schema:
        schema = document["components"]["schemas"][schema["$ref"].split("/")[-1]]
    return schema


def response_error_codes(document, schema):
    schema = resolve(document, schema)
    codes = set()
    for union in ("anyOf", "oneOf"):
        for variant in schema.get(union, []):
            codes.update(response_error_codes(document, variant))
    properties = schema.get("properties", {})
    if "error" in properties:
        codes.update(response_error_codes(document, properties["error"]))
    if "code" in properties:
        code_schema = resolve(document, properties["code"])
        codes.update(code_schema.get("enum", []))
        if "const" in code_schema:
            codes.add(code_schema["const"])
    return codes


def test_all_application_error_subclasses_have_http_statuses(openapi_document):
    # App construction imports the active domains before walking their descendants.
    errors = list(application_errors())
    assert errors
    assert {error.code for error in errors} == APPLICATION_ERROR_STATUSES.keys()
    assert all(400 <= APPLICATION_ERROR_STATUSES[error.code] < 600 for error in errors)


def test_all_application_error_codes_are_documented_at_their_status_in_domain_routes(openapi_document):
    route_families = {
        "voice_archive_api.services.live": "/api/v1/live",
        "voice_archive_api.services.series": "/api/v1/series",
        "voice_archive_api.services.books": "/api/v1/books",
        "voice_archive_api.services.content": "/api/v1/books",
        "voice_archive_api.services.files": "/api/v1/files",
        "voice_archive_api.file_handling": "/api/v1/files",
        "voice_archive_api.api.v1.file_uploads": "/api/v1/files",
    }
    for error in application_errors():
        route_prefix = next(prefix for module, prefix in route_families.items() if error.__module__.startswith(module))
        status = str(APPLICATION_ERROR_STATUSES[error.code])
        codes = set()
        for path, operations in openapi_document["paths"].items():
            if not path.startswith(route_prefix):
                continue
            for operation in operations.values():
                response = operation["responses"].get(status, {})
                schema = response.get("content", {}).get("application/json", {}).get("schema", {})
                codes.update(response_error_codes(openapi_document, schema))
        assert error.code in codes, f"{error.__name__}: missing {status} code in {route_prefix} responses"
