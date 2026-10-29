from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from opentelemetry import propagate, trace
from opentelemetry.trace import Span, SpanKind

TRACER = trace.get_tracer("manufacturing.workflow")


def agent_attributes() -> dict[str, str]:
    name = os.getenv("FOUNDRY_AGENT_NAME") or "manufacturing-hosted"
    version = os.getenv("FOUNDRY_AGENT_VERSION", "")
    result = {
        "gen_ai.agent.name": name,
        "gen_ai.agent.id": f"{name}:{version}" if version else name,
        "gen_ai.provider.name": "azure.ai.openai",
    }
    if version:
        result["gen_ai.agent.version"] = version
    return result


@contextmanager
def operation_span(name: str, operation: str, *, client: bool = False) -> Iterator[Span]:
    attributes = {**agent_attributes(), "gen_ai.operation.name": operation}
    with TRACER.start_as_current_span(
        name, kind=SpanKind.CLIENT if client else SpanKind.INTERNAL, attributes=attributes,
    ) as span:
        span.set_attributes(attributes)
        yield span


def trace_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    propagate.inject(headers)
    return headers
