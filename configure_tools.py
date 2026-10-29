from __future__ import annotations

import json
import subprocess
from tempfile import NamedTemporaryFile

import httpx
from azure.identity import AzureCliCredential

from domain import ROOT
from foundry import CONFIG, ENDPOINT
from azure_cli import az


def azd(*args: str) -> str:
    if args[0] == "ai":
        args = (*args, "--project-endpoint", ENDPOINT)
    result = subprocess.run(
        ["azd", *args], cwd=ROOT / "hosted",
        capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError(f"azd {args[:3]}: {result.stderr.strip()}\n{result.stdout.strip()}")
    return result.stdout


def main() -> None:
    resource_id = (
        f"/subscriptions/{CONFIG['subscription']}/resourceGroups/{CONFIG['resource_group']}"
        f"/providers/Microsoft.Search/searchServices/{CONFIG['search']}"
    )
    target = f"https://{CONFIG['search']}.search.windows.net/knowledgebases/{CONFIG['knowledge_base']}/mcp?api-version=2026-08-01-preview"
    insights = az("monitor", "app-insights", "component", "show",
                  "--subscription", CONFIG["subscription"], "--resource-group", CONFIG["resource_group"],
                  "--app", CONFIG["insights"])
    azd("env", "set", "APPLICATIONINSIGHTS_CONNECTION_STRING", insights["connectionString"])
    azd("env", "set", "TOOLBOX_NAME", CONFIG["toolbox"])
    iq_connection = f"{CONFIG['toolbox']}-iq"
    search_connection = f"{CONFIG['toolbox']}-search"
    azd("ai", "connection", "create", iq_connection, "--kind", "remote-tool",
        "--target", target, "--auth-type", "project-managed-identity",
        "--audience", "https://search.azure.com", "--force", "--no-prompt")
    azd("ai", "connection", "create", search_connection, "--kind", "cognitive-search",
        "--target", f"https://{CONFIG['search']}.search.windows.net", "--auth-type", "project-managed-identity",
        "--metadata", f"ResourceId={resource_id}", "--force", "--no-prompt")
    toolboxes = json.loads(azd("ai", "toolbox", "list", "--output", "json"))
    listing = toolboxes.get("data", toolboxes.get("value", [])) if isinstance(toolboxes, dict) else toolboxes
    if not any(item.get("name") == CONFIG["toolbox"] for item in listing):
        with NamedTemporaryFile(mode="w", suffix=".json") as definition:
            json.dump({
                "description": "Manufacturing procedures through Foundry IQ and MCP.",
                "connections": [{"name": iq_connection}],
            }, definition)
            definition.flush()
            print(azd("ai", "toolbox", "create", CONFIG["toolbox"], "--from-file", definition.name, "--no-prompt"))
    credential = AzureCliCredential(tenant_id=CONFIG["tenant"])
    token = credential.get_token("https://ai.azure.com/.default").token
    endpoint = f"{ENDPOINT}/toolboxes/{CONFIG['toolbox']}/mcp?api-version=v1"
    response = httpx.post(endpoint, headers={"Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
    }, timeout=120)
    response.raise_for_status()
    result = response.json()
    if "error" in result:
        raise RuntimeError(f"MCP tools/list: {result['error']}")
    tools = result["result"]["tools"]
    matches = [tool for tool in tools if "knowledge_base_retrieve" in tool["name"]]
    if len(matches) != 1:
        raise RuntimeError(f"La toolbox no expone IQ: {[tool['name'] for tool in tools]}")
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results/iq-mcp-tools.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(matches[0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
