#!/usr/bin/env python3
"""Translate a city snapshot into deterministic Minecraft structures."""

from typing import Any, Dict, Iterable, List, Tuple

BASE_Y = 4
CITY_ZONE = {
    "min_x": -220,
    "max_x": 220,
    "min_y": 4,
    "max_y": 40,
    "min_z": 64,
    "max_z": 370,
}
MAX_STRUCTURES = 750
MAX_FEATURES = 1500
MAX_OPERATIONS = 5000
MAX_WORKFLOW_LAYERS = 5
WORKFLOW_LAYER_SIZE = 49
MAX_WORKFLOWS_PER_REPOSITORY = (
    MAX_WORKFLOW_LAYERS * WORKFLOW_LAYER_SIZE
)
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
KIND_WIDTH = {
    "machine": 9,
    "daemon": 5,
    "sentinel": 7,
    "repository": 7,
}
SERVICE_Z = {
    "machine": 72,
    "daemon": 84,
    "sentinel": 96,
}
SERVICE_X = tuple(range(-210, 211, 10))
REPOSITORY_X = tuple(range(-195, 196, 10))
REPOSITORY_Z = tuple(range(112, 363, 10))


def short_text(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def flatten(entities: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    for entity in entities:
        yield entity
        yield from flatten(entity.get("children", []))


def previous_origins(
    previous_layout: Dict[str, Any] = None,
) -> Dict[str, Tuple[str, int, int]]:
    origins = {}
    if not isinstance(previous_layout, dict):
        return origins
    for structure in previous_layout.get("structures", []):
        origin = structure.get("origin")
        if (
            isinstance(structure.get("entity_id"), str)
            and structure.get("kind") in KIND_WIDTH
            and isinstance(origin, list)
            and len(origin) == 3
            and all(isinstance(value, int) for value in origin)
        ):
            origins[structure["entity_id"]] = (
                structure["kind"],
                origin[0],
                origin[2],
            )
    return origins


def footprint(kind: str, x: int, z: int) -> Tuple[int, int, int, int]:
    half = KIND_WIDTH[kind] // 2
    return (x - half, x + half, z - half - 1, z + half)


def footprints_overlap(
    left: Tuple[int, int, int, int],
    right: Tuple[int, int, int, int],
) -> bool:
    return not (
        left[1] < right[0]
        or right[1] < left[0]
        or left[3] < right[2]
        or right[3] < left[2]
    )


def valid_origin(kind: str, x: int, z: int) -> bool:
    minimum_x, maximum_x, minimum_z, maximum_z = footprint(kind, x, z)
    if (
        minimum_x < CITY_ZONE["min_x"]
        or maximum_x > CITY_ZONE["max_x"]
        or minimum_z < CITY_ZONE["min_z"]
        or maximum_z > CITY_ZONE["max_z"]
    ):
        return False
    if kind == "repository":
        return z >= REPOSITORY_Z[0]
    return z == SERVICE_Z[kind]


def candidate_origins(kind: str) -> Iterable[Tuple[int, int]]:
    if kind == "repository":
        ordered_x = sorted(REPOSITORY_X, key=lambda value: (abs(value), value))
        for z in REPOSITORY_Z:
            for x in ordered_x:
                yield x, z
        return
    for x in sorted(SERVICE_X, key=lambda value: (abs(value), value)):
        yield x, SERVICE_Z[kind]


def place_kind(
    kind: str,
    entities: List[Dict[str, Any]],
    prior: Dict[str, Tuple[str, int, int]],
) -> List[Tuple[Dict[str, Any], int, int]]:
    placements = {}
    occupied: List[Tuple[int, int, int, int]] = []
    for entity in sorted(entities, key=lambda item: item["id"]):
        previous = prior.get(entity["id"])
        if not previous or previous[0] != kind:
            continue
        _, x, z = previous
        candidate = footprint(kind, x, z)
        if (
            valid_origin(kind, x, z)
            and not any(
                footprints_overlap(candidate, existing)
                for existing in occupied
            )
        ):
            placements[entity["id"]] = (x, z)
            occupied.append(candidate)

    available = list(candidate_origins(kind))
    for entity in sorted(entities, key=lambda item: item["id"]):
        if entity["id"] in placements:
            continue
        for x, z in available:
            candidate = footprint(kind, x, z)
            if any(
                footprints_overlap(candidate, existing)
                for existing in occupied
            ):
                continue
            placements[entity["id"]] = (x, z)
            occupied.append(candidate)
            break
        else:
            raise ValueError(
                f"{kind} district cannot safely place {len(entities)} buildings"
            )
    return [
        (entity, *placements[entity["id"]])
        for entity in entities
    ]


def validate_snapshot_capacity(snapshot: Dict[str, Any]) -> None:
    entities = snapshot.get("entities", [])
    if not entities:
        raise ValueError("city layout must contain at least one structure")
    unknown = sorted({
        str(entity.get("kind"))
        for entity in entities
        if entity.get("kind") not in KIND_WIDTH
    })
    if unknown:
        raise ValueError(f"unsupported top-level entity kinds: {', '.join(unknown)}")

    ids = []
    feature_count = 0
    operation_count = 0
    violations = []
    for entity in entities:
        ids.append(entity.get("id"))
        children = entity.get("children", [])
        operation_count += 3
        if entity["kind"] == "repository":
            feature_count += len(children)
            operation_count += len(children)
            if len(children) > MAX_WORKFLOWS_PER_REPOSITORY:
                violations.append(
                    f"{entity['id']} has {len(children)} workflows "
                    f"(max {MAX_WORKFLOWS_PER_REPOSITORY})"
                )
        elif children:
            violations.append(f"{entity['id']} has unsupported child entities")
        elif entity.get("status") == "critical":
            operation_count += 4
        ids.extend(child.get("id") for child in children)

    invalid_ids = [identifier for identifier in ids if not isinstance(identifier, str)]
    if invalid_ids:
        violations.append("every entity must have a string id")
    elif len(ids) != len(set(ids)):
        violations.append("entity ids must be globally unique")
    if len(entities) > MAX_STRUCTURES:
        violations.append(
            f"{len(entities)} structures exceeds the {MAX_STRUCTURES} limit"
        )
    if feature_count > MAX_FEATURES:
        violations.append(
            f"{feature_count} features exceeds the {MAX_FEATURES} limit"
        )
    if operation_count > MAX_OPERATIONS:
        violations.append(
            f"{operation_count} operations exceeds the {MAX_OPERATIONS} limit"
        )
    if violations:
        raise ValueError("city layout exceeds safety limits: " + "; ".join(violations))


def district_origins(
    snapshot: Dict[str, Any],
    previous_layout: Dict[str, Any] = None,
) -> Dict[str, List[Tuple[Dict[str, Any], int, int]]]:
    validate_snapshot_capacity(snapshot)
    grouped = {"machine": [], "daemon": [], "sentinel": [], "repository": []}
    for entity in snapshot.get("entities", []):
        grouped[entity["kind"]].append(entity)

    prior = previous_origins(previous_layout)
    return {
        kind: place_kind(kind, entities, prior)
        for kind, entities in grouped.items()
    }


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
        for index, child in enumerate(children):
            layer, slot = divmod(index, len(roof_slots))
            if layer >= MAX_WORKFLOW_LAYERS:
                raise ValueError(
                    f"{entity['id']} exceeds workflow-light capacity"
                )
            dx, dz = roof_slots[slot]
            position = (x + dx, y1 + layer + 1, z + dz)
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


def build_layout(
    snapshot: Dict[str, Any],
    previous_layout: Dict[str, Any] = None,
) -> Dict[str, Any]:
    structures = []
    for kind, placements in district_origins(
        snapshot,
        previous_layout=previous_layout,
    ).items():
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
    expected_ids = {
        entity["id"]
        for entity in flatten(snapshot.get("entities", []))
    }
    if set(index) != expected_ids:
        missing = sorted(expected_ids - set(index))
        raise ValueError(
            "layout did not index every entity: "
            + ", ".join(missing[:5])
        )
    feature_count = sum(len(item["features"]) for item in structures)
    operation_count = sum(len(item["operations"]) for item in structures)
    for structure in structures:
        minimum = structure["bounds"]["min"]
        maximum = structure["bounds"]["max"]
        sign = structure["sign"]["position"]
        if (
            minimum[0] < CITY_ZONE["min_x"]
            or maximum[0] > CITY_ZONE["max_x"]
            or minimum[1] < CITY_ZONE["min_y"]
            or maximum[1] > CITY_ZONE["max_y"]
            or minimum[2] < CITY_ZONE["min_z"]
            or maximum[2] > CITY_ZONE["max_z"]
            or sign[0] < CITY_ZONE["min_x"]
            or sign[0] > CITY_ZONE["max_x"]
            or sign[1] < CITY_ZONE["min_y"]
            or sign[1] > CITY_ZONE["max_y"]
            or sign[2] < CITY_ZONE["min_z"]
            or sign[2] > CITY_ZONE["max_z"]
        ):
            raise ValueError(f"{structure['entity_id']} leaves the city zone")
    return {
        "schema": "rapp-infrastructure-city-layout/1",
        "generated_at": snapshot["generated_at"],
        "summary": {
            "structures": len(structures),
            "features": feature_count,
            "operations": operation_count,
            "overall_status": snapshot["summary"]["overall_status"],
        },
        "structures": structures,
        "entity_index": index,
        "legend": STATUS_BLOCKS,
    }
