from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    api_version: Literal["v1"]


class NotReadyResponse(BaseModel):
    status: Literal["unavailable"]
    api_version: Literal["v1"]
