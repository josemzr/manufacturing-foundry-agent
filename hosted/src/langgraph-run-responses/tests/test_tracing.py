import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main

EXPORTER = InMemorySpanExporter()
PROVIDER = TracerProvider()
PROVIDER.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(PROVIDER)


class TraceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        EXPORTER.clear()
        self.carriers = []
        self.fail_iq = False
        self.environment = patch.dict(os.environ, {
            "AZURE_AI_PROJECT_ENDPOINT": "https://demo.services.ai.azure.com/api/projects/demo",
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": "test-model", "TOOLBOX_NAME": "test-tools",
            "KNOWLEDGE_BASE_NAME": "manufacturing-iq", "FOUNDRY_AGENT_NAME": "manufacturing-hosted",
            "FOUNDRY_AGENT_VERSION": "3",
        })
        self.environment.start()
        owner = self

        class Model:
            def with_config(self, _):
                return self

            async def ainvoke(self, messages, **kwargs):
                await asyncio.sleep(0)
                owner.carriers.append(("model", kwargs["extra_headers"]))
                return AIMessage(
                    content="Respuesta PR-PROD-017, PR-ENER-009 y PR-TURNO-004.",
                    usage_metadata={"input_tokens": 20, "output_tokens": 8, "total_tokens": 28},
                )

        class IqTool:
            name = "manufacturing-iq-mcp___knowledge_base_retrieve"
            args_schema = {"properties": {"query_variants": {"type": "array"}}}

            async def ainvoke(self, arguments):
                await asyncio.sleep(0)
                if owner.fail_iq:
                    raise RuntimeError("IQ unavailable")
                return [{"type": "text", "text": "PR-PROD-017 PR-ENER-009 PR-TURNO-004: evidencia de prueba."}]

        class Toolbox:
            def __init__(self, **kwargs):
                owner.carriers.append(("mcp", kwargs["extra_headers"]))

            async def get_tools(self):
                return [IqTool()]

        project = SimpleNamespace(
            get_openai_client=lambda: SimpleNamespace(base_url="https://demo/openai/v1/"),
        )
        self.patches = [
            patch.object(main, "AIProjectClient", return_value=project),
            patch.object(main, "DefaultAzureCredential"),
            patch.object(main, "get_bearer_token_provider", return_value=lambda: "unit-test"),
            patch.object(main, "ChatOpenAI", return_value=Model()),
            patch.object(main, "AzureAIProjectToolbox", Toolbox),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.environment.stop()

    async def invoke(self, action="procedure"):
        graph = await main.build_graph()
        state = await graph.ainvoke({"messages": [HumanMessage(content=json.dumps({
            "action": action, "zone_id": "Z02", "question": "Consulta el procedimiento.",
        }))]})
        return json.loads(state["messages"][-1].content)

    async def test_one_trace_with_real_parent_child_relationships(self):
        result = await self.invoke()
        spans = EXPORTER.get_finished_spans()
        self.assertEqual(len({span.context.trace_id for span in spans}), 1)
        self.assertEqual(len(spans), 4)
        root = next(span for span in spans if span.attributes["gen_ai.operation.name"] == "invoke_agent")
        self.assertEqual(result["trace_id"], format(root.context.trace_id, "032x"))
        children = [span for span in spans if span is not root]
        self.assertTrue(all(span.parent.span_id == root.context.span_id for span in children))
        self.assertEqual({span.attributes["gen_ai.operation.name"] for span in children}, {"execute_tool", "chat"})
        self.assertTrue(any(span.attributes.get("gen_ai.tool.name") == "manufacturing-iq-mcp___knowledge_base_retrieve" for span in children))
        for _, carrier in self.carriers:
            self.assertEqual(carrier["traceparent"].split("-")[1], result["trace_id"])
        self.assertTrue(all(span.attributes["gen_ai.agent.id"] == "manufacturing-hosted:3" for span in spans))
        self.assertTrue(all(span.attributes.get("gen_ai.usage.input_tokens") == 20 for span in children if span.attributes["gen_ai.operation.name"] == "chat"))

    async def test_concurrent_requests_do_not_share_context_or_headers(self):
        first, second = await asyncio.gather(self.invoke(), self.invoke("energy"))
        self.assertNotEqual(first["trace_id"], second["trace_id"])
        groups = {}
        for span in EXPORTER.get_finished_spans():
            groups.setdefault(format(span.context.trace_id, "032x"), []).append(span)
        self.assertEqual(set(groups), {first["trace_id"], second["trace_id"]})
        self.assertTrue(all(len(spans) == 4 for spans in groups.values()))
        carriers = [carrier["traceparent"].split("-")[1] for _, carrier in self.carriers]
        self.assertEqual(carriers.count(first["trace_id"]), 2)
        self.assertEqual(carriers.count(second["trace_id"]), 2)

    async def test_failed_retrieval_is_an_error_and_never_calls_the_model(self):
        self.fail_iq = True
        with self.assertRaisesRegex(RuntimeError, "IQ unavailable"):
            await self.invoke()
        spans = EXPORTER.get_finished_spans()
        self.assertFalse(any(span.attributes["gen_ai.operation.name"] == "chat" for span in spans))
        errors = [span for span in spans if span.status.status_code == StatusCode.ERROR]
        self.assertEqual(len(errors), 2)
        self.assertFalse(any(kind == "model" for kind, _ in self.carriers))


if __name__ == "__main__":
    unittest.main()
