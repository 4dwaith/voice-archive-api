import pytest


@pytest.fixture
def openapi_document(schema_only_client):
    response = schema_only_client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()
