"""Structured domain errors matching API_CONTRACTS.md."""
from __future__ import annotations

from typing import Any
from fastapi import HTTPException


class DomainError(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            status_code=status_code,
            detail={
                "code": code,
                "message": message,
                "details": details or {},
            },
            headers=headers,
        )
        self.code = code
        self.message = message
        self.details = details or {}
