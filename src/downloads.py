"""Typed download instructions returned by APK providers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DownloadSpec:
    """A URL plus request headers required by its origin."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("download URL must be a non-empty string")
        object.__setattr__(self, "headers", dict(self.headers))


def normalize_download(value: str | DownloadSpec) -> DownloadSpec:
    if isinstance(value, DownloadSpec):
        return value
    return DownloadSpec(url=value)
