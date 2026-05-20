"""Common request/response schemas matching the Java API response format."""
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ResponseWrapper(BaseModel, Generic[T]):
    """Matches Java controller response: Map.of("code", 200, "message", "success", "data", ...)"""
    code: int = 200
    message: str = "success"
    data: T | None = None
