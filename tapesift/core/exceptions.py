"""Exception hierarchy for TapeSift.

Every user-facing failure should be raised as (or wrapped into) one of these,
carrying a human-readable message plus an optional suggested next step.
"""

from __future__ import annotations


class TapeSiftError(Exception):
    """Base class for all TapeSift errors."""

    def __init__(self, message: str, suggestion: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion

    def user_text(self) -> str:
        if self.suggestion:
            return f"{self.message}\n\n{self.suggestion}"
        return self.message


class FFmpegNotFoundError(TapeSiftError):
    pass


class FFprobeNotFoundError(TapeSiftError):
    pass


class InvalidVideoError(TapeSiftError):
    pass


class TimestampParseError(TapeSiftError):
    pass


class InvalidRangeError(TapeSiftError):
    pass


class MissingSourceError(TapeSiftError):
    pass


class OutputFolderError(TapeSiftError):
    pass


class ExportError(TapeSiftError):
    pass


class ExportCancelledError(TapeSiftError):
    pass


class HardwareEncoderError(ExportError):
    pass


class DatabaseError(TapeSiftError):
    pass


class CsvImportError(TapeSiftError):
    pass
