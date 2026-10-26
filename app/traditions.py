"""Loads traditions.json and layers a regional variant on top of a religion."""
import copy
import json
from functools import lru_cache

from .config import DATA_DIR

CONSTRAINT_TYPES = ["dietary", "dress", "timing", "who_pays"]


@lru_cache(maxsize=1)
def kb() -> dict:
    return json.loads((DATA_DIR / "traditions.json").read_text())


def options() -> dict:
    k = kb()
    return {
        "religions": [{"key": key, "label": r["label"]} for key, r in k["religions"].items()],
        "variants": [
            {"key": key, "label": v["label"], "applies_to": v["applies_to"]}
            for key, v in k["variants"].items()
        ],
    }


def validate_selection(religion: str, variant) -> None:
    k = kb()
    if religion not in k["religions"]:
        raise ValueError(f"Unknown religion: {religion}")
    if variant:
        if variant not in k["variants"]:
            raise ValueError(f"Unknown regional variant: {variant}")
        if religion not in k["variants"][variant]["applies_to"]:
            raise ValueError(f"{k['variants'][variant]['label']} is not available with {k['religions'][religion]['label']}")


def effective(religion: str, variant=None) -> dict:
    """Religion base + variant layered on top.

    Functions with the same key are overridden by the variant. Constraints are appended
    (each tagged with its source). Task templates are concatenated.
    """
    validate_selection(religion, variant)
    k = kb()
    base = copy.deepcopy(k["religions"][religion])
    label = base["label"]
    functions = {f["key"]: f for f in base["functions"]}
    constraints = {t: [dict(c, source=base["label"]) for c in base["constraints"].get(t, [])] for t in CONSTRAINT_TYPES}
    tasks = list(base["task_templates"])
    confirm = list(base["confirm_with"])

    if variant:
        v = copy.deepcopy(k["variants"][variant])
        label = f"{v['label']} {base['label']}"
        for f in v.get("functions", []):
            functions[f["key"]] = {**functions.get(f["key"], {}), **f}
        for t in CONSTRAINT_TYPES:
            constraints[t] += [dict(c, source=v["label"]) for c in v.get("constraints", {}).get(t, [])]
        tasks += v.get("task_templates", [])
        confirm += [c for c in v.get("confirm_with", []) if c not in confirm]

    fn_list = sorted(functions.values(), key=lambda f: (f["order"], f["key"]))
    return {
        "religion": religion,
        "variant": variant,
        "label": label,
        "confirm_with": confirm,
        "functions": fn_list,
        "constraints": constraints,
        "task_templates": tasks,
    }


def generic_tasks() -> list:
    return kb()["generic_tasks"]
