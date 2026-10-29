from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential, ManagedIdentityCredential, get_bearer_token_provider
from openai import OpenAI

from domain import ACTIONS, ROOT, context

CONFIG_FILE = Path(os.environ.get("MANUFACTURING_CONFIG_FILE", str(ROOT / "config.json")))
if not CONFIG_FILE.is_file():
    raise RuntimeError("Falta config.json. Copia config.example.json y configura tus recursos de Azure.")
CONFIG = json.loads(CONFIG_FILE.read_text())
ENDPOINT = f"https://{CONFIG['account']}.services.ai.azure.com/api/projects/{CONFIG['project']}"
HOSTED_BASE = f"{ENDPOINT}/agents/{CONFIG['agent']}/endpoint/protocols/openai/"


def credential():
    client_id = os.getenv("AZURE_CLIENT_ID")
    if client_id:
        return ManagedIdentityCredential(client_id=client_id)
    return AzureCliCredential(tenant_id=CONFIG["tenant"], process_timeout=30)


def project_client() -> AIProjectClient:
    return AIProjectClient(endpoint=ENDPOINT, credential=credential())


def hosted_client() -> OpenAI:
    return OpenAI(
        base_url=HOSTED_BASE,
        api_key=get_bearer_token_provider(credential(), "https://ai.azure.com/.default"),
        default_query={"api-version": "v1"}, timeout=180, max_retries=1,
    )


def validate_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise RuntimeError("El hosted agent no devolvió un objeto válido.")
    iq = result.get("iq")
    if (
        not isinstance(result.get("answer"), str) or not result["answer"].strip()
        or not isinstance(iq, dict) or iq.get("verified") is not True
        or iq.get("knowledge_base") != CONFIG["knowledge_base"]
        or not isinstance(iq.get("retrieved_text"), str)
        or iq.get("document_id") not in ("PR-PROD-017", "PR-ENER-009", "PR-TURNO-004")
        or iq["document_id"] not in iq["retrieved_text"]
    ):
        raise RuntimeError("La respuesta no acredita recuperación real desde Foundry IQ.")
    tools = result.get("tools")
    if not isinstance(tools, list) or not any(
        isinstance(tool, dict) and tool.get("source") == "foundry_iq_ai_search"
        for tool in tools
    ):
        raise RuntimeError("Falta la evidencia MCP de la consulta a Foundry IQ.")
    if result.get("approval") != "pending_human_review" or result.get("live_foundry") is not True:
        raise RuntimeError("La respuesta no conserva el estado de revisión humana.")
    return result


def run(action: str, zone_id: str, question: str = "") -> dict[str, Any]:
    if action not in ACTIONS:
        raise ValueError("Acción no permitida.")
    if not isinstance(question, str) or len(question) > 1200:
        raise ValueError("La pregunta debe tener como máximo 1.200 caracteres.")
    context(zone_id)
    with hosted_client() as client:
        response = client.responses.create(
            input=json.dumps({"action": action, "zone_id": zone_id, "question": question}, ensure_ascii=False),
        )
        if response.status != "completed" or not response.output_text.strip():
            raise RuntimeError("El hosted agent no ha completado la respuesta.")
        try:
            result = validate_result(json.loads(response.output_text))
        except json.JSONDecodeError as error:
            raise RuntimeError("El hosted agent no devolvió el formato de respuesta esperado.") from error
        if result.get("action") != action or result.get("zone_id") != zone_id:
            raise RuntimeError("La respuesta pertenece a otra acción o zona.")
        result["response_id"] = response.id
    directory = Path(os.environ.get("RESULTS_DIR", str(ROOT / "results")))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{action}-{response.id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result
