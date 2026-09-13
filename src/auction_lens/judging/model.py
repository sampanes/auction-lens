"""Asking a locally served model one question at a time.

Reached over HTTP the same way the mail server and the webhook are, which is
what keeps it from becoming a dependency: nothing is imported, nothing is
installed, and a machine with nothing listening simply does not vet.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .verdicts import Verdict, verdict_from

# Ollama unloads the weights between requests unless told otherwise. A run
# asks a few hundred questions, and reloading several gigabytes between each
# one costs far more than answering does.
KEEP_LOADED = "10m"

# Nothing creative is wanted. The same lot asked twice should answer twice the
# same way, because a digest that changes its mind between runs cannot be
# debugged by looking at it.
SETTLED = {"temperature": 0, "top_p": 1}


class ModelUnavailable(RuntimeError):
    """Nothing is serving the model, so this run cannot vet at all."""


@dataclass(frozen=True)
class LocalModel:
    """One locally served model, addressed by where it listens."""

    endpoint: str
    model: str
    timeout_seconds: int = 60

    @property
    def chat_url(self) -> str:
        return f"{self.endpoint.rstrip('/')}/api/chat"

    @property
    def tags_url(self) -> str:
        return f"{self.endpoint.rstrip('/')}/api/tags"

    def reachable(self) -> bool:
        """Whether anything answers, asked once before a run commits to it.

        Checked up front so an unreachable server costs one failed request
        rather than one per lot, and so the report can say plainly that
        nothing was vetted instead of quietly showing everything.
        """
        try:
            with urllib.request.urlopen(self.tags_url, timeout=self.timeout_seconds):
                return True
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    def verdict(self, instructions: str, subject: str) -> Verdict:
        """One answer, or a kept lot when the answer cannot be understood.

        An unreadable answer keeps the lot. The two mistakes are not equal:
        showing one extra baby monitor costs a line in an email, while hiding
        a real find costs the thing itself, and silence is how the old term
        lists failed. When in doubt this errs towards showing you too much.
        """
        try:
            answer = self._ask(instructions, subject)
        except (urllib.error.URLError, OSError, TimeoutError) as problem:
            raise ModelUnavailable(str(problem)) from problem
        return verdict_from(answer)

    def _ask(self, instructions: str, subject: str) -> str:
        """The raw reply text for one question."""
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": subject},
                ],
                "stream": False,
                "format": "json",
                "keep_alive": KEEP_LOADED,
                "options": SETTLED,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.chat_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.load(response)["message"]["content"]
