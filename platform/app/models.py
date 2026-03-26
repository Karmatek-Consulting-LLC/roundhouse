from __future__ import annotations

import re
from pydantic import BaseModel, field_validator


class TemplateVariable(BaseModel):
    name: str
    description: str
    default: str | None = None
    required: bool = False


class TemplateResponse(BaseModel):
    name: str
    description: str
    variables: list[TemplateVariable]


class CreateServerRequest(BaseModel):
    name: str
    template: str
    config: dict[str, str] = {}

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$", v):
            raise ValueError(
                "Name must be lowercase alphanumeric with hyphens, "
                "1-64 chars, cannot start/end with a hyphen"
            )
        return v


class ServerResponse(BaseModel):
    name: str
    template: str
    status: str
    url: str
    created_at: str | None = None
