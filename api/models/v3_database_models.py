"""Strict request contracts for approved V3 database cross-read checks."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DatabaseQueryRegistrationRequest(_StrictModel):
    query_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=255)
    sql: str = Field(min_length=1, max_length=10000)
    parameter_names: list[str] = Field(default_factory=list, max_length=20)
    database_ref: Literal["configured-test"] = "configured-test"
    environment: Literal["test", "staging", "production"] = "test"
    approved: bool = False

    @field_validator("query_id")
    @classmethod
    def validate_query_id(cls, value: str) -> str:
        if not _ID_PATTERN.fullmatch(value):
            raise ValueError("query ID may contain only letters, numbers, '_' and '-'")
        return value

    @field_validator("parameter_names")
    @classmethod
    def validate_parameter_names(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("parameter names must be unique")
        for value in values:
            if not _ID_PATTERN.fullmatch(value):
                raise ValueError("parameter names must be safe identifiers")
        return values


class DatabaseReadRequest(_StrictModel):
    parameters: dict[str, Any] = Field(default_factory=dict)
    api_value: Any | None = None
    api_result: Literal["ok", "blocked"] = "ok"
    tolerance: str = Field(default="0", max_length=80)

    @field_validator("tolerance")
    @classmethod
    def validate_tolerance(cls, value: str) -> str:
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError):
            raise ValueError("tolerance must be a finite non-negative decimal")
        if not parsed.is_finite() or parsed < 0:
            raise ValueError("tolerance must be a finite non-negative decimal")
        return value
