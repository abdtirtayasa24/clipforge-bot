from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PermissionEvidence(BaseModel):
    """Why the admin may process this media (spec: only creator-authorized sources)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["own_content", "creator_permission", "license"]
    statement: str = Field(min_length=10, max_length=2000)
    reference: str | None = Field(default=None, max_length=500)
