"use strict";

const $ = (id) => document.getElementById(id);
let data;
let selectedZone = "Z02";
let action = "overview";
let busy = false;
const labels = {
  overview: ["01 / PRODUCCIÓN", "Estado de las líneas", "¿Qué líneas necesitan revisión este turno?"],
  energy: ["02 / ENERGÍA", "Comparar alternativas", "Compara las ventanas A y B de este escenario."],
  brief: ["04 / PARTE DE TURNO", "Resumen para el equipo", "Prepara el parte de turno con situación y pendientes."],
  procedure: ["03 / PROCEDIMIENTOS", "Consulta documental", "¿Qué procedimiento debemos seguir ante una desviación del tiempo de ciclo?"],
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setAction(value) {
  action = value;
  document.querySelectorAll(".step").forEach((step) => {
    step.classList.toggle("active", step.dataset.action === action);
    step.setAttribute("aria-pressed", String(step.dataset.action === action));
  });
  ["overview", "energy", "procedure", "brief"].forEach((name) => {
    $(name === "overview" ? "map-view" : `${name}-view`).classList.toggle("hidden", name !== action);
  });
  $("panel-eyebrow").textContent = labels[action][0];
  $("panel-title").textContent = labels[action][1];
  $("question-text").textContent = labels[action][2];
}

function selectZone(id) {
  selectedZone = id;
  const zone = data.zones.find((row) => row.id === id);
  $("zone-id").textContent = `${zone.id} / SELECCIÓN`;
  $("zone-name").textContent = zone.name;
  $("zone-status").textContent = zone.status;
  $("zone-deviation").textContent = `${zone.deviation_pct >= 0 ? "+" : ""}${zone.deviation_pct} %`;
  $("baseline-value").textContent = `${zone.baseline_cycle_seconds} s/pieza`;
  $("observed-value").textContent = `${zone.observed_cycle_seconds} s/pieza`;
  const max = Math.max(zone.baseline_cycle_seconds, zone.observed_cycle_seconds) * 1.15;
  $("baseline-bar").style.width = `${zone.baseline_cycle_seconds / max * 100}%`;
  $("observed-bar").style.width = `${zone.observed_cycle_seconds / max * 100}%`;
  document.querySelectorAll("[data-zone]").forEach((node) => {
    node.classList.toggle("selected", node.dataset.zone === id);
    if (node.tagName === "BUTTON") node.setAttribute("aria-pressed", String(node.dataset.zone === id));
  });
}

function renderMap() {
  const namespace = "http://www.w3.org/2000/svg";
  for (const zone of data.zones) {
    const group = document.createElementNS(namespace, "g");
    group.setAttribute("class", `map-point ${zone.needs_review ? "review" : ""}`);
    group.dataset.zone = zone.id;
    group.setAttribute("transform", `translate(${zone.x},${zone.y})`);
    for (const [radius, className] of [[zone.needs_review ? 32 : 23, "halo"], [11, "dot"]]) {
      const circle = document.createElementNS(namespace, "circle");
      circle.setAttribute("r", String(radius));
      circle.setAttribute("class", className);
      group.append(circle);
    }
    for (const [text, y, className] of [[zone.name, -42, ""], [`${zone.deviation_pct >= 0 ? "+" : ""}${zone.deviation_pct} %`, 48, "point-value"]]) {
      const label = document.createElementNS(namespace, "text");
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("y", String(y));
      label.setAttribute("class", className);
      label.textContent = text;
      group.append(label);
    }
    group.addEventListener("click", () => selectZone(zone.id));
    $("map-points").append(group);
    const button = element("button", "", zone.name);
    button.type = "button";
    button.dataset.zone = zone.id;
    button.addEventListener("click", () => selectZone(zone.id));
    $("zone-buttons").append(button);
  }
}

function renderEnergy() {
  const comparison = data.energy_comparison;
  $("renewable-delta").replaceChildren(
    document.createTextNode(`+${comparison.renewable_delta_pp} `), element("b", "", "pp"));
  $("cost-delta").replaceChildren(
    document.createTextNode(`${comparison.difference_eur.toLocaleString("es-ES")} € `),
    element("span", "", `· ${comparison.difference_pct.toLocaleString("es-ES")} %`));
  $("energy-renewable-delta").replaceChildren(
    document.createTextNode(`+${comparison.renewable_delta_pp} pp `), element("span", "", "renovable"));
  for (const plan of data.energy_comparison.plans) {
    const card = element("article", `plan ${plan.id === "B" ? "alternative" : ""}`);
    card.append(element("small", "", `HIPÓTESIS ${plan.id}`), element("h3", "", plan.name));
    const price = element("div", "price", `${plan.cost_eur} `);
    price.append(element("span", "", "€"));
    card.append(price, element("p", "", `${plan.price_eur_kwh.toLocaleString("es-ES")} €/kWh · precio ficticio`));
    const track = element("div", "renewable-track");
    const bar = element("div", "renewable-bar");
    bar.style.width = `${plan.renewable_pct}%`;
    track.append(bar);
    card.append(track, element("p", "renewable-label", `${plan.renewable_pct} % renovable`), element("p", "", "Condición de servicio supuesta, no validada."));
    $("energy-plans").append(card);
  }
}

function renderAnswerText(text) {
  $("answer").replaceChildren();
  for (const paragraph of text.split(/\n{2,}/)) {
    const node = element("p");
    for (const part of paragraph.split(/(\*\*[^*]+\*\*)/g)) {
      node.append(part.startsWith("**") && part.endsWith("**")
        ? element("strong", "", part.slice(2, -2))
        : document.createTextNode(part));
    }
    $("answer").append(node);
  }
}

async function ask() {
  if (busy || !data) return;
  busy = true;
  const requestedAction = action;
  const requestedZone = selectedZone;
  $("ask-button").disabled = true;
  $("ask-text").textContent = "Consultando…";
  $("progress").classList.remove("hidden");
  $("answer-error").classList.add("hidden");
  $("answer-panel").classList.add("hidden");
  $("answer-empty").classList.add("hidden");
  $("live-status").textContent = "Consultando";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 180000);
  try {
    const response = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: requestedAction, zone_id: requestedZone, question: $("extra-question").value }),
      signal: controller.signal,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(`${result.error}${result.detail ? " " + result.detail : ""}`);
    if (!result.live_foundry || !result.answer || !result.response_id) throw new Error("Respuesta no verificable de Foundry.");
    renderAnswerText(result.answer);
    const zone = data.zones.find((row) => row.id === requestedZone);
    $("answer-scope").textContent = `${labels[requestedAction][0]} · ${zone.name} · ${new Date(result.generated_at).toLocaleTimeString("es-ES")}`;
    $("response-id").textContent = result.response_id;
    $("result-agent").textContent = result.agent_version ? `${result.agent} · v${result.agent_version}` : result.agent;
    const validTrace = typeof result.trace_id === "string" && /^[0-9a-f]{32}$/.test(result.trace_id) && !/^0+$/.test(result.trace_id);
    $("trace-id").textContent = validTrace ? result.trace_id : "No disponible para esta consulta";
    $("copy-trace-id").disabled = !validTrace;
    if (!result.iq || result.iq.verified !== true) throw new Error("No hay evidencia verificada de Foundry IQ.");
    $("procedure-document").textContent = result.iq.document_id;
    $("procedure-origin").textContent = `${result.iq.knowledge_base} · ${result.iq.transport}`;
    $("procedure-excerpt").textContent = result.iq.retrieved_text;
    $("tool-chips").replaceChildren(...result.tools.map((tool) => element("span", "", `✓ ${tool.name} · ${tool.mode}`)));
    const sources = [element("h4", "", "FUENTES CITADAS · DATOS Y CONTEXTO SEPARADOS")];
    for (const source of result.sources.filter((item) => result.answer.includes(item.id))) {
      const node = element(source.url ? "a" : "p", "", `[${source.id}] ${source.title}${source.kind === "synthetic" ? " · ficticio" : " · público"}`);
      if (source.url) {
        node.href = source.url;
        node.target = "_blank";
        node.rel = "noopener noreferrer";
      }
      sources.push(node);
    }
    $("sources").replaceChildren(...sources);
    $("answer-panel").classList.remove("hidden");
    $("live-status").textContent = "En vivo ✓";
  } catch (error) {
    $("answer-error").textContent = error.name === "AbortError"
      ? "La consulta ha superado tres minutos. No hay respuesta simulada. Revisa el terminal y vuelve a intentarlo."
      : error.message;
    $("answer-error").classList.remove("hidden");
    $("live-status").textContent = "Error · revisar";
  } finally {
    clearTimeout(timer);
    busy = false;
    $("ask-button").disabled = false;
    $("ask-text").textContent = "Preguntar a Foundry";
    $("progress").classList.add("hidden");
  }
}

document.querySelectorAll(".step").forEach((step) => step.addEventListener("click", () => setAction(step.dataset.action)));
$("ask-button").addEventListener("click", ask);
$("theme-toggle").addEventListener("click", () => {
  document.documentElement.dataset.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
});
$("copy-id").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("response-id").textContent);
    $("copy-status").textContent = "ID copiado.";
  } catch {
    $("copy-status").textContent = "No se pudo copiar. Selecciona el ID y cópialo manualmente.";
  }
});
$("copy-trace-id").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("trace-id").textContent);
    $("copy-status").textContent = "Trace ID copiado. Úsalo para abrir la ejecución completa en Foundry o Application Insights.";
  } catch {
    $("copy-status").textContent = "No se pudo copiar. Selecciona el Trace ID manualmente.";
  }
});

async function initialize() {
  try {
    const response = await fetch("/api/bootstrap");
    if (!response.ok) throw new Error("No se ha podido cargar el escenario.");
    data = await response.json();
    $("project-name").textContent = data.foundry.agent;
    $("total-flow").textContent = String(data.average_cycle_seconds);
    $("review-count").textContent = String(data.review_count).padStart(2, "0");
    $("snapshot-label").textContent = data.label;
    renderMap();
    renderEnergy();
    selectZone(selectedZone);
    setAction(action);
    $("ask-button").disabled = false;
  } catch (error) {
    $("load-error").textContent = `${error.message} Revisa el servidor local; no hay datos de respaldo ocultos.`;
    $("load-error").classList.remove("hidden");
    $("ask-button").disabled = true;
  }
}
initialize();
