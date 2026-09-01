"""Authority load/validation errors and diagnostics helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from governance.config_contract.errors import DiagnosticError
    from governance.config_contract.models import CanonicalConfig

CODE_PARSE = "parse_error"
CODE_MISSING = "missing_authority_file"
CODE_UNSUPPORTED = "unsupported_authority_version"
CODE_SCHEMA = "schema_validation_failed"
CODE_SEMANTIC = "semantic_validation_failed"
CODE_DUPLICATE = "duplicate_authority_rule_id"
CODE_AMBIGUOUS = "ambiguous_authority_selector"


@dataclass(frozen=True, slots=True)
class AuthorityDiagnosticError:
    code: str
    path: str
    message: str
    source: str = ""
    file_index: int | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "source": self.source,
        }


class AuthorityError(Exception):
    """Base class for authority file validation failures."""

    def __init__(
        self, errors: list[AuthorityDiagnosticError] | tuple[AuthorityDiagnosticError, ...]
    ) -> None:
        self.errors = tuple(sorted(errors, key=lambda e: (e.source, e.path, e.code, e.message)))
        super().__init__(self.errors[0].message if self.errors else "invalid authority")


class AuthorityParseError(AuthorityError):
    """YAML parse or file read failure."""


class AuthoritySchemaError(AuthorityError):
    """JSON Schema structural failure."""


class AuthoritySemanticError(AuthorityError):
    """Semantic validation failure."""


class UnsupportedAuthorityVersionError(AuthorityError):
    """Unsupported authority_version."""


def map_authority_exception_to_config_diagnostics(
    exc: AuthorityError,
    *,
    canonical: CanonicalConfig | None = None,
) -> list[DiagnosticError]:
    """Map an AuthorityError into config DiagnosticError list for CLI/Action."""
    from governance.config_contract.errors import DiagnosticError

    source_to_index: dict[str, int] = {}
    if canonical is not None:
        for index, relative in enumerate(canonical.authority.files):
            source_to_index[relative.replace("\\", "/")] = index

    mapped: list[DiagnosticError] = []
    for error in sorted(exc.errors, key=lambda e: (e.source, e.path, e.code, e.message)):
        if error.code == CODE_PARSE:
            code = "parse_error"
        elif error.code in {CODE_SCHEMA, CODE_UNSUPPORTED}:
            code = "schema_validation_failed"
        else:
            code = "semantic_validation_failed"

        file_index = error.file_index
        if file_index is None and error.source:
            file_index = source_to_index.get(error.source)
        outer_path = (
            f"/authority/files/{file_index}" if file_index is not None else "/authority/files"
        )
        message = error.message
        if error.path:
            message = f"{message} ({error.path})"
        if error.source:
            message = f"{message} [source={error.source}]"
        mapped.append(DiagnosticError(code=code, path=outer_path, message=message))
    return mapped
