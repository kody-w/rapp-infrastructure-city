#!/usr/bin/env python3
"""Translate a city snapshot into deterministic Minecraft structures."""

import math
from typing import Any, Dict, Iterable, List, Tuple

from city_model import SEVERITY

BASE_Y = 4
STATUS_BLOCKS = {
    "healthy": "minecraft:emerald_block",
    "active": "minecraft:diamond_block",
    "warning": "minecraft:gold_block",
    "critical": "minecraft:redstone_block",
    "offline": "minecraft:coal_block",
    "unknown": "minecraft:quartz_block",
}
KIND_BASE = {
    "machine": "minecraft:iron_block",
    "daemon": "minecraft:copper_block",
    "sentinel": "minecraft:amethyst_block",
    "repository": "minecraft:stone_bricks",
}


def short_text(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def flatten(entities: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    for entity in entities:
        yield entity
        yield from flatten(entity.get("children", []))


def district_origins(snapshot: Dict[str, Any]) -> Dict[str, List[Tuple[Dict[str, Any], int, int]]]:
    grouped = {"machine": [], "daemon": [], "sentinel": [], "repository": []}
    for entity in snapshot.get("entities", []):
        grouped.setdefault(entity["kind"], []).append(entity)

    result: Dict[str, List[Tuple[Dict[str, Any], int, int]]] = {
        key: [] for key in grouped
    }
    for index, entity in enumerate(grouped.get("machine", [])):
        result["machine"].append((entity, -200 + index * 12, 72))
    for index, entity in enumerate(grouped.get("daemon", [])):
        result["daemon"].append((entity, -200 + index * 10, 84))
    for index, entity in enumerate(grouped.get("sentinel", [])):
        result["sentinel"].append((entity, -200 + index * 12, 96))

    repos = grouped.get("repository", [])
    columns = max(1, min(39, math.ceil(math.sqrt(len(repos)))))
    spacing = 10
    start_x = -((columns - 1) * spacing) // 2
    for index, entity in enumerate(repos):
        row, column = divmod(index, columns)
        result["repository"].append(
            (entity, start_x + column * spacing, 112 + row * spacing)
        )
    return result


def fill(start, end, block):
    return {"op": "fill", "from": list(start), "to": list(end), "block": block}


def setblock(position, block):
    return {"op": "setblock", "position": list(position), "block": block}


def building(entity: Dict[str, Any], x: int, z: int) -> Dict[str, Any]:
    kind = entity["kind"]
    children = entity.get("children", [])
    if kind == "repository":
        width = 7
        height = 5 + min(14, max(0, len(children) // 4))
    elif kind == "machine":
        width, height = 9, 12
    elif kind == "sentinel":
        width, height = 7, 10
    else:
        width, height = 5, 7

    half = width // 2
    y0, y1 = BASE_Y, BASE_Y + height
    shell = KIND_BASE.get(kind, "minecraft:stone_bricks")
    status_block = STATUS_BLOCKS[entity["status"]]
    operations = [
        {
            **fill(
                (x - half, y0, z - half),
                (x + half, y1, z + half),
                shell,
            ),
            "mode": "hollow",
        },
        fill((x - half, y1, z - half), (x + half, y1, z + half), status_block),
        fill((x - 1, y0 + 1, z - half), (x + 1, y0 + 3, z - half), "minecraft:air"),
    ]

    features = []
    if kind == "repository":
        roof_slots = [
            (dx, dz)
            for dz in range(-half, half + 1)
            for dx in range(-half, half + 1)
        ]
        for child, (dx, dz) in zip(children, roof_slots):
            position = (x + dx, y1 + 1, z + dz)
            operations.append(
                setblock(position, STATUS_BLOCKS[child["status"]])
            )
            features.append(
                {
                    "entity_id": child["id"],
                    "name": child["name"],
                    "status": child["status"],
                    "position": list(position),
                    "evidence": child.get("evidence", []),
                    "repairs": child.get("repairs", []),
                }
            )
    elif entity["status"] == "critical":
        for dy in range(1, 5):
            operations.append(
                setblock((x, y1 + dy, z), "minecraft:redstone_lamp")
            )

    sign_lines = [
        short_text(entity["name"], 30),
        entity["kind"].upper(),
        entity["status"].upper(),
        short_text(
            (entity.get("evidence") or [{}])[0].get("detail", "no evidence"),
            50,
        ),
    ]
    return {
        "id": f"building:{entity['id']}",
        "entity_id": entity["id"],
        "kind": kind,
        "name": entity["name"],
        "status": entity["status"],
        "origin": [x, BASE_Y, z],
        "bounds": {
            "min": [x - half, y0, z - half],
            "max": [x + half, y1 + 5, z + half],
        },
        "operations": operations,
        "sign": {
            "position": [x, y0 + 1, z - half - 1],
            "lines": sign_lines,
        },
        "features": features,
        "evidence": entity.get("evidence", []),
        "repairs": entity.get("repairs", []),
    }


def build_layout(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    structures = []
    for kind, placements in district_origins(snapshot).items():
        for entity, x, z in placements:
            structures.append(building(entity, x, z))

    structures.sort(key=lambda item: item["entity_id"])
    index = {}
    for structure in structures:
        index[structure["entity_id"]] = {
            "building_id": structure["id"],
            "position": structure["origin"],
            "kind": structure["kind"],
        }
        for feature in structure["features"]:
            index[feature["entity_id"]] = {
                "building_id": structure["id"],
                "position": feature["position"],
                "kind": "workflow",
            }
    return {
        "schema": "rapp-infrastructure-city-layout/1",
        "generated_at": snapshot["generated_at"],
        "summary": {
            "structures": len(structures),
            "features": sum(len(item["features"]) for item in structures),
            "operations": sum(len(item["operations"]) for item in structures),
            "overall_status": snapshot["summary"]["overall_status"],
        },
        "structures": structures,
        "entity_index": index,
        "legend": STATUS_BLOCKS,
    }
