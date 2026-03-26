from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, field_validator


# --- Templates (kept for backward compat) ---


class TemplateVariable(BaseModel):
    name: str
    description: str
    default: str | None = None
    required: bool = False


class TemplateResponse(BaseModel):
    name: str
    description: str
    variables: list[TemplateVariable]


# --- MCP Primitives ---


class ToolParameter(BaseModel):
    name: str
    type: str = "str"
    description: str = ""
    required: bool = True
    default: str | None = None


class ToolPrimitive(BaseModel):
    kind: Literal["tool"] = "tool"
    name: str
    description: str = ""
    parameters: list[ToolParameter] = []
    code: str = ""


class ResourcePrimitive(BaseModel):
    kind: Literal["resource"] = "resource"
    name: str
    uri: str
    description: str = ""
    mime_type: str = "text/plain"
    code: str = ""


class ResourceTemplatePrimitive(BaseModel):
    kind: Literal["resource_template"] = "resource_template"
    name: str
    uri_template: str
    description: str = ""
    mime_type: str = "text/plain"
    code: str = ""


class PromptPrimitive(BaseModel):
    kind: Literal["prompt"] = "prompt"
    name: str
    description: str = ""
    parameters: list[ToolParameter] = []
    code: str = ""


Primitive = ToolPrimitive | ResourcePrimitive | ResourceTemplatePrimitive | PromptPrimitive


# --- Server ---


class EnvVar(BaseModel):
    name: str
    value: str


class ServerSpec(BaseModel):
    """Persisted server definition with its primitives."""
    name: str
    description: str = ""
    imports: list[str] = []
    primitives: list[Primitive] = []
    pip_packages: list[str] = []
    env_vars: list[EnvVar] = []


class CreateServerRequest(BaseModel):
    name: str
    description: str = ""
    template: str | None = None
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
    description: str = ""
    imports: list[str] = []
    primitives: list[Primitive] = []
    pip_packages: list[str] = []
    env_vars: list[EnvVar] = []
    owner_id: str | None = None
    owner_email: str | None = None
    created_at: str | None = None


class AddPrimitiveRequest(BaseModel):
    primitive: Primitive


class UpdatePipPackagesRequest(BaseModel):
    pip_packages: list[str]


class UpdateEnvVarsRequest(BaseModel):
    env_vars: list[EnvVar]


class UpdateConfigRequest(BaseModel):
    imports: list[str] = []
    pip_packages: list[str] = []
    env_vars: list[EnvVar] = []


# --- Auth ---


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str
    role: str = "user"


class TeamRequest(BaseModel):
    name: str
    description: str = ""


class TeamMemberRequest(BaseModel):
    user_id: str
    role: str = "member"


class TeamMemberResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str


class TeamResponse(BaseModel):
    id: str
    name: str
    description: str
    members: list[TeamMemberResponse] = []
