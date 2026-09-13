"""Persistence boundary for reusable Signature identity templates.

The in-memory implementation is deliberately pure.  A later database or
file-backed adapter can implement the same contract after its migration is
coordinated with the rest of the application.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from tapesift.models.signature_template import SignatureTemplate


class SignatureTemplateRepositoryError(ValueError):
    """A repository operation would make the template collection ambiguous."""


class DuplicateSignatureTemplateNameError(SignatureTemplateRepositoryError):
    """Two templates cannot have the same user-facing name."""


@runtime_checkable
class SignatureTemplateRepository(Protocol):
    """Storage contract used by a future Template Manager."""

    def save(self, template: SignatureTemplate) -> SignatureTemplate:
        """Insert or replace a template by stable id."""
        ...

    def get(self, template_id: str) -> SignatureTemplate | None:
        """Return one template without manufacturing a default."""
        ...

    def get_by_name(self, name: str) -> SignatureTemplate | None:
        """Look up a user-facing name case-insensitively."""
        ...

    def list_all(self) -> tuple[SignatureTemplate, ...]:
        """Return every template in deterministic display order."""
        ...

    def delete(self, template_id: str) -> bool:
        """Delete an existing template and report whether anything changed."""
        ...


class InMemorySignatureTemplateRepository:
    """Reference implementation of the repository contract."""

    def __init__(self, templates: Iterable[SignatureTemplate] = ()) -> None:
        self._templates: dict[str, SignatureTemplate] = {}
        for template in templates:
            self.save(template)

    @staticmethod
    def _name_key(name: str) -> str:
        return name.casefold()

    def save(self, template: SignatureTemplate) -> SignatureTemplate:
        if not isinstance(template, SignatureTemplate):
            raise SignatureTemplateRepositoryError(
                "repository accepts only SignatureTemplate instances"
            )
        name_key = self._name_key(template.name)
        for existing_id, existing in self._templates.items():
            if existing_id != template.template_id and self._name_key(
                existing.name
            ) == name_key:
                raise DuplicateSignatureTemplateNameError(
                    f"a template named {template.name!r} already exists"
                )
        self._templates[template.template_id] = template
        return template

    def get(self, template_id: str) -> SignatureTemplate | None:
        return self._templates.get(template_id)

    def get_by_name(self, name: str) -> SignatureTemplate | None:
        if not isinstance(name, str):
            return None
        name_key = self._name_key(name)
        return next(
            (
                template
                for template in self._templates.values()
                if self._name_key(template.name) == name_key
            ),
            None,
        )

    def list_all(self) -> tuple[SignatureTemplate, ...]:
        return tuple(
            sorted(
                self._templates.values(),
                key=lambda template: (
                    self._name_key(template.name),
                    template.name,
                    template.template_id,
                ),
            )
        )

    def delete(self, template_id: str) -> bool:
        if template_id not in self._templates:
            return False
        del self._templates[template_id]
        return True
