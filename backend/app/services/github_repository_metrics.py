from __future__ import annotations

from dataclasses import dataclass, field
import logging
from time import perf_counter


logger = logging.getLogger(__name__)


@dataclass
class GithubRepositoryRequestAttempt:
    conditional: bool
    operation: str
    _started_at: float = field(default_factory=perf_counter)
    _finished: bool = False

    def finish(
        self,
        *,
        etag_received: bool,
        outcome: str,
        response_bytes: int,
        status: str | int,
    ) -> None:
        if self._finished:
            return
        self._finished = True
        duration_ms = max(int((perf_counter() - self._started_at) * 1000), 0)
        logger.info(
            "github_operation=%s conditional=%s duration_ms=%d outcome=%s "
            "status=%s response_bytes=%d etag_received=%s",
            self.operation,
            str(self.conditional).lower(),
            duration_ms,
            outcome,
            status,
            max(response_bytes, 0),
            str(etag_received).lower(),
        )


def github_operation(path: str) -> str:
    if "/git/trees/" in path:
        return "repository_tree"
    if "/contents/" in path:
        return "repository_file"
    if path.startswith("/user/repos"):
        return "repository_list"
    if path.startswith("/repos/"):
        return "repository_metadata"
    return "other"
