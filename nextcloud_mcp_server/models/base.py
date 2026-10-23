"""Base Pydantic models for common response patterns."""

from datetime import datetime, timezone
from typing import Optional, Union

from pydantic import BaseModel, Field, field_serializer


def _utc_now() -> datetime:
    """Generate UTC timestamp for responses."""
    return datetime.now(timezone.utc)


class BaseResponse(BaseModel):
    """Base response model for all MCP tool responses."""

    success: bool = Field(
        default=True, description="Whether the operation was successful"
    )
    timestamp: datetime = Field(
        default_factory=_utc_now, description="Response timestamp"
    )

    @field_serializer("timestamp")
    def serialize_timestamp(self, timestamp: datetime) -> str:
        """Serialize timestamp to RFC3339 format for MCP compliance."""
        if timestamp.tzinfo is None:
            # If somehow we get a naive datetime, assume UTC
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        # Use isoformat() which produces RFC3339 compliant format
        # For UTC times, replace '+00:00' with 'Z' as preferred by many systems
        iso_string = timestamp.isoformat()
        if iso_string.endswith("+00:00"):
            return iso_string[:-6] + "Z"
        return iso_string


class IdResponse(BaseResponse):
    """Response model for operations that return a new ID."""

    id: Union[int, str] = Field(description="ID of the created or affected resource")


class StatusResponse(BaseResponse):
    """Response model for operations that return just a status."""

    status_code: Optional[int] = Field(None, description="HTTP status code")
    message: Optional[str] = Field(None, description="Status message")
