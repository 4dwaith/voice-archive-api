"""Query routing and validation for dense vector search."""

import math


def resolve_query_texts(language: str, query: str, query_en: str) -> dict[str, str]:
    return {"primary": query_en} if language == "en" else {"primary": query, "translated": query_en}


def validate_query_vector(vector: list[float], dimensions: int) -> None:
    if (
        len(vector) != dimensions
        or not all(math.isfinite(value) for value in vector)
        or not 0 < math.hypot(*vector) < math.inf
    ):
        raise ValueError("Invalid query embedding dimensions, values or magnitude")
