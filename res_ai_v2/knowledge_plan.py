from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .normalize import sha256_parts
from .pit_store import observation_groups
from .structure import CURRENT_STRUCTURE

AGENT_TASK_TYPES = {
    "mapping_conflict",
    "missing_context",
    "duplicate_observation",
    "import_issue",
    "directive_challenge",
}


@dataclass(frozen=True)
class MappingSpec:
    address_key: str
    res_name: str
    branch_name: str
    status: str
    confidence: float
    observations: list[dict[str, Any]]


@dataclass(frozen=True)
class KnowledgePlan:
    rows: list[dict[str, Any]]
    valid_rows: list[dict[str, Any]]
    groups: dict[str, list[dict[str, Any]]]
    mappings: list[MappingSpec]
    tasks: list[dict[str, Any]]
    keep_keys: set[str]
    directive_keys: set[str]


def _trust(occurrences: int, contexts: int) -> float:
    return round(
        max(
            1.0,
            min(
                99.9,
                99.9 / max(1, occurrences),
                99.9 / max(1, contexts),
            ),
        ),
        1,
    )


def _anchor(row: dict[str, Any]) -> tuple[str, str]:
    if row.get("settlement_key"):
        return "settlement", str(row["settlement_key"])
    return "locality", str(row.get("locality_key", ""))


def _address_key(row: dict[str, Any]) -> str:
    return sha256_parts(
        [
            str(row.get("locality_key", "")),
            str(row.get("district_key", "")),
            str(row.get("settlement_key", "")),
            str(row.get("street_key", "")),
        ]
    )


def _address_payload(row: dict[str, Any], address_key: str | None = None) -> dict[str, Any]:
    return {
        "address_key": address_key or _address_key(row),
        "locality": row.get("locality", ""),
        "district": row.get("district", ""),
        "settlement": row.get("settlement", ""),
        "street": row.get("street", ""),
    }


def _selected_res(directive: dict[str, Any]) -> set[str]:
    return {
        str(value)
        for value in (directive.get("selection") or {}).get("selected_res", [])
        if value
    }


def _challenge(
    base: dict[str, Any],
    directive: dict[str, Any],
    directives: dict[str, dict[str, Any]],
    current_version: int,
    relevant: set[str],
) -> dict[str, Any] | None:
    base_key = str(base["task_key"])
    relevant.add(base_key)
    observed = {
        str(option.get("res", ""))
        for option in (base.get("payload") or {}).get("options", [])
        if option.get("res")
    }
    selected = _selected_res(directive)
    if current_version <= int(directive.get("source_version", 0)) or observed.issubset(selected):
        return None

    challenge_key = sha256_parts(
        ["directive_challenge", base_key, sha256_parts(sorted(observed | selected))]
    )
    previous = directives.get(challenge_key)
    if previous:
        relevant.add(challenge_key)
        previous_selected = _selected_res(previous)
        if (
            current_version <= int(previous.get("source_version", 0))
            or observed.issubset(previous_selected)
        ):
            return None
        selected = previous_selected
        challenge_key = sha256_parts(
            ["directive_challenge", base_key, sha256_parts(sorted(observed | selected))]
        )

    return {
        "task_key": challenge_key,
        "task_type": "directive_challenge",
        "subject_type": base["subject_type"],
        "subject_key": base["subject_key"],
        "title": "Новые данные противоречат прежнему решению",
        "payload": {
            **(base.get("payload") or {}),
            "previous_selection": sorted(selected),
            "allow_address_edit": True,
        },
        "priority": 110,
    }


def _resolve_task(
    base: dict[str, Any],
    directives: dict[str, dict[str, Any]],
    current_version: int,
    relevant: set[str],
) -> dict[str, Any] | None:
    directive = directives.get(str(base["task_key"]))
    if not directive:
        return base
    return _challenge(base, directive, directives, current_version, relevant)


def build_knowledge_plan(
    rows: list[dict[str, Any]],
    directives: dict[str, dict[str, Any]],
    current_version: int,
) -> KnowledgePlan:
    valid = [
        row
        for row in rows
        if (row.get("locality_key") or row.get("settlement_key"))
        and str(row.get("res_name", "")) in CURRENT_STRUCTURE
    ]
    valid_ids = {int(row["id"]) for row in valid}
    invalid = [row for row in rows if int(row["id"]) not in valid_ids]
    groups = observation_groups(valid)

    contexts: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)
    rows_by_anchor: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in valid:
        anchor = _anchor(row)
        if not anchor[1]:
            continue
        contexts[anchor].add(
            (
                str(row.get("district_key", "")),
                str(row.get("locality_key", "")),
                str(row.get("settlement_key", "")),
            )
        )
        rows_by_anchor[anchor].append(row)

    mappings: list[MappingSpec] = []
    tasks: list[dict[str, Any]] = []
    keep_keys: set[str] = set()
    directive_keys: set[str] = set()

    def add_task(base: dict[str, Any]) -> None:
        candidate = _resolve_task(base, directives, current_version, directive_keys)
        if candidate:
            keep_keys.add(str(candidate["task_key"]))
            tasks.append(candidate)

    for address_key, group in groups.items():
        by_res: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in group:
            by_res[str(row["res_name"])].append(row)
        conflict = len(by_res) > 1
        if conflict:
            add_task(
                {
                    "task_key": sha256_parts(["mapping_conflict", address_key]),
                    "task_type": "mapping_conflict",
                    "subject_type": "address",
                    "subject_key": address_key,
                    "title": "Один адрес связан с разными РЭС",
                    "payload": {
                        "address": _address_payload(group[0], address_key),
                        "options": [
                            {
                                "branch": CURRENT_STRUCTURE[res],
                                "res": res,
                                "occurrences": sum(
                                    int(item.get("occurrence_count", 1)) for item in items
                                ),
                            }
                            for res, items in sorted(by_res.items())
                        ],
                        "allow_multiple": True,
                        "allow_address_edit": True,
                    },
                    "priority": 100,
                }
            )

        context_count = len(contexts.get(_anchor(group[0]), set())) or 1
        for res_name, observations in by_res.items():
            occurrence_count = sum(
                int(item.get("occurrence_count", 1)) for item in observations
            )
            duplicate = occurrence_count > 1
            mappings.append(
                MappingSpec(
                    address_key=address_key,
                    res_name=res_name,
                    branch_name=CURRENT_STRUCTURE[res_name],
                    status="conflict"
                    if conflict
                    else ("source_only" if duplicate else "consistent"),
                    confidence=_trust(occurrence_count, context_count),
                    observations=observations,
                )
            )
            if duplicate and not conflict:
                add_task(
                    {
                        "task_key": sha256_parts(
                            ["duplicate_observation", address_key, res_name]
                        ),
                        "task_type": "duplicate_observation",
                        "subject_type": "address",
                        "subject_key": address_key,
                        "title": "Адрес повторяется в исходных данных",
                        "payload": {
                            "address": _address_payload(observations[0], address_key),
                            "current": {
                                "branch": CURRENT_STRUCTURE[res_name],
                                "res": res_name,
                            },
                            "options": [
                                {"branch": CURRENT_STRUCTURE[res_name], "res": res_name}
                            ],
                            "occurrences": occurrence_count,
                            "allow_multiple": False,
                            "allow_address_edit": True,
                        },
                        "priority": 85,
                    }
                )

    for row in invalid:
        task_key = sha256_parts(["import_issue", str(row["observation_key"])])
        if task_key in directives:
            directive_keys.add(task_key)
            continue
        keep_keys.add(task_key)
        tasks.append(
            {
                "task_key": task_key,
                "task_type": "import_issue",
                "subject_type": "observation",
                "subject_key": str(row["id"]),
                "title": "Не удалось определить адрес или РЭС",
                "payload": {
                    "observation_id": row["id"],
                    "address": _address_payload(row),
                    "raw_text": row.get("raw_text", ""),
                    "options": [],
                    "allow_multiple": False,
                    "allow_address_edit": True,
                },
                "priority": 95,
            }
        )

    for anchor, anchor_contexts in contexts.items():
        if len(anchor_contexts) <= 1:
            continue
        for row in rows_by_anchor[anchor]:
            if row.get("district_key"):
                continue
            address_key = _address_key(row)
            task_key = sha256_parts(["missing_context", address_key, str(row["res_name"])])
            if task_key in directives:
                directive_keys.add(task_key)
                continue
            keep_keys.add(task_key)
            tasks.append(
                {
                    "task_key": task_key,
                    "task_type": "missing_context",
                    "subject_type": "observation",
                    "subject_key": str(row["id"]),
                    "title": "Не указан район",
                    "payload": {
                        "observation_id": row["id"],
                        "address": _address_payload(row, address_key),
                        "current": {
                            "branch": row.get("branch_name", ""),
                            "res": row.get("res_name", ""),
                        },
                        "options": [
                            {
                                "district": option.get("district", ""),
                                "locality": option.get("locality", ""),
                                "settlement": option.get("settlement", ""),
                                "street": option.get("street", ""),
                                "branch": option.get("branch_name", ""),
                                "res": option.get("res_name", ""),
                            }
                            for option in rows_by_anchor[anchor]
                            if option.get("district_key")
                        ],
                        "allow_multiple": False,
                        "allow_address_edit": True,
                    },
                    "priority": 90,
                }
            )

    return KnowledgePlan(
        rows=rows,
        valid_rows=valid,
        groups=groups,
        mappings=mappings,
        tasks=tasks,
        keep_keys=keep_keys,
        directive_keys=directive_keys,
    )
