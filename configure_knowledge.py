from __future__ import annotations

import json
import time
from typing import Any

import httpx
from azure.identity import AzureCliCredential

from domain import ROOT
from azure_cli import az
from foundry import CONFIG

SEARCH_ENDPOINT = f"https://{CONFIG['search']}.search.windows.net"
VERSION = "2026-08-01-preview"
CREDENTIAL = AzureCliCredential(tenant_id=CONFIG["tenant"])


def search(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    token = CREDENTIAL.get_token("https://search.azure.com/.default").token
    response = httpx.request(
        method, f"{SEARCH_ENDPOINT}/{path}?api-version={VERSION}",
        headers={"Authorization": f"Bearer {token}"}, json=body, timeout=120,
    )
    if response.is_error:
        raise RuntimeError(f"Search {method} {path}: {response.status_code} {response.text}")
    return response.json() if response.content else None


def save(name: str, result: Any) -> None:
    directory = ROOT / "results"
    directory.mkdir(exist_ok=True)
    (directory / name).write_text(json.dumps(result, ensure_ascii=False, indent=2))


def setup() -> None:
    subscription = CONFIG["subscription"]
    group = CONFIG["resource_group"]
    search_info = az("search", "service", "show", "--subscription", subscription,
                     "--resource-group", group, "--name", CONFIG["search"])
    project_id = (
        f"/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.CognitiveServices/"
        f"accounts/{CONFIG['account']}/projects/{CONFIG['project']}"
    )
    project = az("resource", "show", "--ids", project_id, "--api-version", "2025-06-01")
    user = az("ad", "signed-in-user", "show")
    for principal, kind, roles in (
        (user["id"], "User", ["Search Service Contributor", "Search Index Data Contributor"]),
        (project["identity"]["principalId"], "ServicePrincipal", ["Search Index Data Reader"]),
    ):
        for role in roles:
            az("role", "assignment", "create", "--assignee-object-id", principal,
               "--assignee-principal-type", kind, "--role", role, "--scope", search_info["id"])
    # Role propagation is asynchronous; verify data-plane access before creating content.
    for attempt in range(18):
        token = CREDENTIAL.get_token("https://search.azure.com/.default").token
        check = httpx.get(f"{SEARCH_ENDPOINT}/indexes?api-version={VERSION}",
                          headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if check.is_success:
            break
        if check.status_code != 403:
            raise RuntimeError(f"Search access failed: {check.status_code} {check.text}")
        if attempt == 17:
            raise TimeoutError("Search RBAC no se ha propagado tras tres minutos.")
        time.sleep(10)
    index = {
        "name": CONFIG["index"],
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "title", "type": "Edm.String", "searchable": True},
            {"name": "content", "type": "Edm.String", "searchable": True},
            {"name": "source", "type": "Edm.String", "filterable": True},
        ],
        "semantic": {
            "defaultConfiguration": "manufacturing-semantic",
            "configurations": [{
                "name": "manufacturing-semantic",
                "prioritizedFields": {
                    "titleField": {"fieldName": "title"},
                    "prioritizedContentFields": [{"fieldName": "content"}],
                    "prioritizedKeywordsFields": [],
                },
            }],
        },
    }
    search("PUT", f"indexes/{CONFIG['index']}", index)
    documents = []
    for path in sorted((ROOT / "data/procedimientos").glob("*.md")):
        text = path.read_text()
        documents.append({
            "@search.action": "upload", "id": path.stem,
            "title": text.splitlines()[0].removeprefix("# "),
            "content": text, "source": path.name,
        })
    if len(documents) != 3:
        raise RuntimeError("Se esperaban tres procedimientos.")
    uploaded = search("POST", f"indexes/{CONFIG['index']}/docs/index", {"value": documents})
    if any(not row["status"] for row in uploaded["value"]):
        raise RuntimeError(f"Falló la indexación: {uploaded}")
    source = {
        "name": CONFIG["knowledge_source"], "kind": "searchIndex",
        "description": "Procedimientos ficticios de producción, energía y parte de turno.",
        "resultsProcessing": "none",
        "searchIndexParameters": {
            "searchIndexName": CONFIG["index"],
            "semanticConfigurationName": "manufacturing-semantic",
            "searchFields": [{"name": "content"}],
            "sourceDataFields": [{"name": "id"}, {"name": "title"}, {"name": "content"}, {"name": "source"}],
        },
    }
    search("PUT", f"knowledgesources/{CONFIG['knowledge_source']}", source)
    search("PUT", f"knowledgebases/{CONFIG['knowledge_base']}", {
        "name": CONFIG["knowledge_base"], "description": "Foundry IQ: procedimientos de manufacturing y energía.",
        "knowledgeSources": [{"name": CONFIG["knowledge_source"]}],
        "outputMode": "extractiveData", "retrievalReasoningEffort": {"kind": "minimal"},
    })
    result = search("POST", f"knowledgebases/{CONFIG['knowledge_base']}/retrieve", {
        "intents": [{"type": "semantic", "search": "PR-PROD-017 procedimiento desviación tiempo ciclo doble validación registro"}],
        "includeActivity": True,
        "knowledgeSourceParams": [{
            "kind": "searchIndex", "knowledgeSourceName": CONFIG["knowledge_source"],
            "resultsProcessing": "none", "includeReferences": True, "includeReferenceSourceData": True,
        }],
    })
    text = json.dumps(result, ensure_ascii=False)
    if "PR-PROD-017" not in text or "doble validación" not in text:
        raise RuntimeError("La base no ha recuperado el procedimiento esperado.")
    save("iq-retrieve.json", result)
    save("iq-definition.json", search("GET", f"knowledgebases/{CONFIG['knowledge_base']}"))
    save("search-definition.json", {
        "id": search_info["id"], "region": search_info["location"],
        "sku": search_info["sku"]["name"], "index": CONFIG["index"], "documents": len(documents),
    })
    print("AI Search y Foundry IQ verificados: PR-PROD-017 recuperado desde el índice.")


if __name__ == "__main__":
    setup()
