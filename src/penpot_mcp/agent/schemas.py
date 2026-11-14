"""Pydantic v2 Schema-Driven Development (SDD) contracts for the agentic layer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ProviderCatalogEntry(BaseModel):
    label: str
    models: list[str] = Field(default_factory=list)
    default_base_url: str


class QuickActionSpec(BaseModel):
    id: str
    label: str
    prompt: str


class AgentChatRequestSchema(BaseModel):
    prompt: str = Field(default="", max_length=32000)
    quick_action: str | None = None
    session_id: str = Field(default="default", min_length=1, max_length=64)
    attachment_ids: list[str] = Field(default_factory=list)
    import_penpot: bool = False
    sandbox_persistent: bool = False


class AgentDbConfigSummary(BaseModel):
    backend: Literal["penpot_postgres", "postgres", "sqlite"] = "penpot_postgres"
    isolated: bool = False
    target: str = "penpot-postgres"
