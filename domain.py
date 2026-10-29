from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def scenario() -> dict[str, Any]:
    return json.loads((ROOT / "data/scenario.json").read_text())


def zone_summary(zone: dict[str, Any], threshold: float) -> dict[str, Any]:
    baseline = Decimal(str(zone["baseline_cycle_seconds"]))
    if baseline <= 0:
        raise ValueError("La referencia debe ser positiva.")
    deviation = (Decimal(str(zone["observed_cycle_seconds"])) - baseline) / baseline * 100
    return {
        **zone,
        "deviation_pct": float(round(deviation, 1)),
        "needs_review": deviation >= Decimal(str(threshold)),
        "status": "Revisión pendiente" if deviation >= Decimal(str(threshold)) else "Sin señal destacada",
    }


def energy_comparison(data: dict[str, Any]) -> dict[str, Any]:
    comparison = data["energy_comparison"]
    energy = Decimal(str(comparison["energy_kwh"]))
    plans = []
    for plan in comparison["plans"]:
        plans.append({
            **plan,
            "cost_eur": float(energy * Decimal(str(plan["price_eur_kwh"]))),
            "renewable_kwh": float(energy * Decimal(str(plan["renewable_pct"])) / 100),
        })
    reference, alternative = plans
    if not reference["service_eligible"] or not alternative["service_eligible"]:
        raise ValueError("Las hipótesis comparadas no cumplen la condición ficticia de servicio.")
    difference = Decimal(str(reference["cost_eur"])) - Decimal(str(alternative["cost_eur"]))
    return {
        **comparison, "plans": plans,
        "difference_eur": float(difference),
        "difference_pct": float(round(difference / Decimal(str(reference["cost_eur"])) * 100, 1)),
        "renewable_delta_pp": alternative["renewable_pct"] - reference["renewable_pct"],
        "energy_saved_kwh": 0,
        "status": "Hipótesis pendiente de validación humana; no ejecutar",
    }


def bootstrap() -> dict[str, Any]:
    data = scenario()
    zones = [zone_summary(zone, data["review_threshold_pct"]) for zone in data["zones"]]
    return {
        **data, "zones": zones, "energy_comparison": energy_comparison(data),
        "review_count": sum(zone["needs_review"] for zone in zones),
        "average_cycle_seconds": round(sum(zone["observed_cycle_seconds"] for zone in zones) / len(zones), 1),
    }


def context(zone_id: str) -> dict[str, Any]:
    data = bootstrap()
    matches = [zone for zone in data["zones"] if zone["id"] == zone_id]
    if len(matches) != 1:
        raise ValueError("Zona desconocida.")
    return {
        "scenario": data["id"], "data_mode": data["data_mode"],
        "fabric_connected": False, "selected_zone": matches[0],
        "zones": data["zones"], "energy_comparison": data["energy_comparison"],
        "sources": data["sources"], "disclaimer": data["disclaimer"],
    }


ACTIONS = {
    "overview": "Resume qué líneas merecen revisión en este snapshot. Ensamblaje registra 118 frente a 100 segundos por pieza: +18 % de tiempo de ciclo. Separa observación, hipótesis y dato pendiente. No diagnostiques averías ni defectos. Cita IDs de fuentes.",
    "energy": "Compara las hipótesis A y B usando los cálculos de la herramienta. Indica 90 EUR frente a 60 EUR, diferencia ilustrativa 30 EUR y 40 puntos porcentuales de renovable; no hay ahorro de kWh. No son ahorros reales. Cita fuentes.",
    "brief": "Prepara un borrador breve de resumen interno para el equipo: observación, comprobaciones, comparación de hipótesis y siguiente decisión humana. No afirme que se ha enviado ni ejecutado nada. Cita fuentes.",
    "procedure": "¿Qué procedimiento debemos seguir ante una desviación del tiempo de ciclo? Recupera PR-PROD-017 desde Foundry IQ y explica los pasos de doble validación del registro, comparabilidad y revisión humana. Cita el identificador del documento y no ejecutes nada.",
}
