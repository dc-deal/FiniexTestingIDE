"""
API response types for the FiniexTestingIDE HTTP API.

Pydantic models are used here (exception to the project-wide @dataclass rule)
because FastAPI requires Pydantic for automatic OpenAPI schema generation
and response serialization.
"""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str


class BrokerListResponse(BaseModel):
    brokers: list[str]
