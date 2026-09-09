"""Target specs: one YAML file per site under targets/.

A spec is data, not code, so adding a site is a pull request that touches one
file and its fixtures. The loader validates on read and caches, because a
malformed spec must fail at job submission time rather than inside a worker.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

TARGETS_DIR = Path(__file__).resolve().parents[2] / "targets"


class Pagination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["query_param", "next_link", "offset"] = "query_param"
    param: str | None = None
    start: int = 1
    step: int = 1
    next_selector: str | None = None


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_selector: str
    fields: dict[str, str] = Field(description="column name -> CSS selector, relative to the row")
    attributes: dict[str, str] = Field(
        default_factory=dict, description="column name -> 'selector@attr'"
    )
    row_key: list[str] = Field(
        default_factory=list,
        description="columns forming the stable identity used by incremental runs",
    )


class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    start_url: HttpUrl
    fetcher: Literal["http", "browser"] = "http"
    method: Literal["GET", "POST"] = "GET"
    # Documented reason the fetcher is what it is; printed in the README table
    # so nobody reaches for a browser when an HTTP request would do.
    fetcher_reason: str = ""
    respect_robots: bool = True
    rate_per_second: float = 1.0
    wait_for_selector: str | None = None
    pagination: Pagination = Field(default_factory=Pagination)
    extraction: Extraction
    api_alternative: str | None = Field(
        default=None, description="public API covering the same data, if one exists"
    )
    params: dict[str, Any] = Field(default_factory=dict)


class UnknownTarget(KeyError):
    pass


@lru_cache
def load_target(name: str, directory: Path | None = None) -> TargetSpec:
    base = directory or TARGETS_DIR
    path = (base / f"{name}.yml").resolve()
    # Defence in depth: JobCreate already restricts the character set.
    if base.resolve() not in path.parents:
        raise UnknownTarget(name)
    if not path.is_file():
        raise UnknownTarget(name)
    with path.open("r", encoding="utf-8") as fh:
        return TargetSpec.model_validate(yaml.safe_load(fh))


def list_targets(directory: Path | None = None) -> list[str]:
    base = directory or TARGETS_DIR
    return sorted(p.stem for p in base.glob("*.yml"))
