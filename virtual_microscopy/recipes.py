"""Strict local recipes and deterministic case construction; never acquire on load."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from .datasets import DatasetStore, canonical_json, checked_id, json_sha256, now_iso, validate_dataset_paths
from .hbm import compose_hbm
from .recipe_schemas import CaseProposal, RecipeCreate, RecipeFromDataset, RecipeRecord
from .sam_volume import estimate_sam
from .volume_jobs import _connection
from .volume_schemas import SamVolumeRequest

MAX_RECIPE_BYTES = 8 * 1024 * 1024


def _check_record(record: dict) -> dict:
    encoded = canonical_json(record)  # Also rejects NaN/Infinity in arbitrary historical JSON.
    if len(encoded) > MAX_RECIPE_BYTES:
        raise ValueError("Recipe exceeds the 8 MiB JSON limit.")
    validated = RecipeRecord.model_validate(record).model_dump(mode="json")
    if validated != record:
        raise ValueError("Imported recipe must contain its complete exported fields without normalization.")
    if record["parent_recipe_id"] == record["recipe_id"]:
        raise ValueError("Recipe cannot be its own parent revision.")
    if json_sha256(record["request"]) != record["request_sha256"]:
        raise ValueError("Recipe request checksum mismatch.")
    if json_sha256({key: value for key, value in record.items() if key != "recipe_sha256"}) != record["recipe_sha256"]:
        raise ValueError("Recipe record checksum mismatch.")
    request = record["request"]
    if set(request) != {"twin", "acquisition"} or not isinstance(request["twin"], dict) or not isinstance(request["acquisition"], dict):
        raise ValueError("Recipe must contain a frozen SAM twin and acquisition object.")
    objects = request["twin"].get("objects")
    if not isinstance(objects, list) or len(objects) > 600:
        raise ValueError("A recipe twin must contain at most 600 primitives.")
    provenance = record["provenance"]
    if provenance is not None:
        source = provenance["source_manifest"]
        if (json_sha256(source) != provenance["source_manifest_sha256"] or
                source.get("dataset_id") != provenance["source_dataset_id"] or
                source.get("input_sha256") != provenance["source_input_sha256"] or
                source.get("request") != request or source.get("kind", "sam_rf_volume") != "sam_rf_volume" or
                source.get("complete") is not True or source.get("state") != "completed"):
            raise ValueError("Recipe source provenance mismatch or incomplete SAM source.")
    return record


def _check_gate(gate, request):
    if gate is None:
        return
    from .sam_volume import _layout
    acquisition = request["acquisition"]
    # Use the same final saved sample and inclusive tolerance as saved gate processing.
    count = _layout(SamVolumeRequest.model_validate(request))["nt"]
    first = acquisition["record_start_us"]
    last = first + (count - 1) / acquisition["sample_rate_mhz"]
    if gate["start_us"] < first - 1e-9 or gate["end_us"] > last + 1e-9:
        raise ValueError("Default gate must lie within the acquisition's saved sample times.")
    import math
    lo = max(0, math.ceil((gate["start_us"] - first - 1e-9) * acquisition["sample_rate_mhz"]))
    hi = min(count - 1, math.floor((gate["end_us"] - first + 1e-9) * acquisition["sample_rate_mhz"]))
    if hi < lo:
        raise ValueError("Default gate contains no saved RF samples.")


class RecipeStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with _connection(self.root) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS recipes (recipe_id TEXT PRIMARY KEY, record_json TEXT NOT NULL)")
            connection.commit()

    def get(self, identifier: str) -> dict:
        with _connection(self.root) as connection:
            row = connection.execute("SELECT record_json FROM recipes WHERE recipe_id=?", (checked_id(identifier),)).fetchone()
        if row is None:
            raise KeyError(identifier)
        return _check_record(json.loads(row["record_json"]))

    def list(self) -> list[dict]:
        with _connection(self.root) as connection:
            rows = connection.execute("SELECT record_json FROM recipes").fetchall()
        summaries = []
        for row in rows:
            record = _check_record(json.loads(row["record_json"]))
            summaries.append({key: record[key] for key in ("recipe_id", "name", "created_at", "parent_recipe_id", "request_sha256")} |
                             {"source_dataset_id": (record["provenance"] or {}).get("source_dataset_id")})
        return sorted(summaries, key=lambda item: (item["created_at"], item["recipe_id"]), reverse=True)

    def import_record(self, record: dict) -> dict:
        record = _check_record(record)
        with _connection(self.root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute("SELECT record_json FROM recipes WHERE recipe_id=?", (record["recipe_id"],)).fetchone()
            if prior is not None:
                if json.loads(prior["record_json"]) != record:
                    raise ValueError("Recipe ID already exists with different immutable content.")
            else:
                # New nonhistorical imports must meet the current constructor
                # and bounds. Keep already-saved and source-provenanced records
                # readable even if their geometry predates the current schema.
                if record["provenance"] is None:
                    SamVolumeRequest.model_validate(record["request"])
                    _check_gate(record["default_gate"], record["request"])
                connection.execute("INSERT INTO recipes VALUES (?,?)", (record["recipe_id"], canonical_json(record).decode("utf-8")))
            connection.commit()
        return deepcopy(record)

    def _save(self, name, request, default_gate=None, parent_recipe_id=None, provenance=None):
        record = {"kind": "sam_acquisition_recipe", "schema_version": 1, "recipe_id": str(uuid4()),
                  "parent_recipe_id": parent_recipe_id, "name": name, "created_at": now_iso(), "request": request,
                  "default_gate": default_gate, "provenance": provenance, "request_sha256": json_sha256(request)}
        record["recipe_sha256"] = json_sha256(record)
        return self.import_record(record)

    def create(self, body: RecipeCreate | dict):
        body = body if isinstance(body, RecipeCreate) else RecipeCreate.model_validate(body)
        if body.parent_recipe_id:
            self.get(body.parent_recipe_id)
        request = body.request.model_dump(mode="json", exclude_none=True)
        gate = body.default_gate.model_dump() if body.default_gate else None
        _check_gate(gate, request)
        return self._save(body.name, request, gate, body.parent_recipe_id)

    def from_dataset(self, body: RecipeFromDataset | dict):
        body = body if isinstance(body, RecipeFromDataset) else RecipeFromDataset.model_validate(body)
        store = DatasetStore(self.root)
        path = store.path(body.dataset_id)
        if not path.is_dir():
            raise KeyError(body.dataset_id)
        validate_dataset_paths(path, body.dataset_id, include_arrays=False)
        initial = store.manifest(body.dataset_id)
        if initial.get("kind", "sam_rf_volume") != "sam_rf_volume" or not initial.get("complete") or initial.get("state") != "completed":
            raise ValueError("A source recipe requires a completed SAM RF dataset.")
        validate_dataset_paths(path, body.dataset_id)
        manifest = store.verify_complete(body.dataset_id)
        gate = body.default_gate.model_dump() if body.default_gate else None
        if gate:
            # Historical coordinates are authoritative. No current constructor.
            import numpy as np
            time = np.asarray(store.open_arrays(body.dataset_id)["time_us"][:], dtype=float)
            if gate["start_us"] < time[0] - 1e-9 or gate["end_us"] > time[-1] + 1e-9:
                raise ValueError("Default gate must lie within the saved sample times.")
            if not np.any((time >= gate["start_us"] - 1e-9) & (time <= gate["end_us"] + 1e-9)):
                raise ValueError("Default gate contains no saved RF samples.")
        provenance = {"source_dataset_id": body.dataset_id, "source_input_sha256": manifest["input_sha256"],
                      "source_manifest_sha256": json_sha256(manifest), "source_manifest": manifest}
        return self._save(body.name, manifest["request"], gate, provenance=provenance)


def _json_differences(before, after, path=""):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [change for key in sorted(set(before) | set(after))
                for change in _json_differences(before.get(key), after.get(key), f"{path}/{key}")]
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [change for index, (a, b) in enumerate(zip(before, after))
                for change in _json_differences(a, b, f"{path}/{index}")]
    return [{"path": path, "before": before, "after": after}]


def case_differences(reference: dict, candidate: dict) -> dict:
    acq_a, acq_b = reference["acquisition"], candidate["acquisition"]
    settings = [{"field": key, "before": acq_a.get(key), "after": acq_b.get(key)}
                for key in sorted(set(acq_a) | set(acq_b)) if acq_a.get(key) != acq_b.get(key)]
    a, b = ({item["id"]: item for item in value["twin"]["objects"]} for value in (reference, candidate))
    primitives = [{"id": key, "change": "added" if key not in a else "removed" if key not in b else "modified",
                   "before": a.get(key), "after": b.get(key)}
                  for key in sorted(set(a) | set(b)) if a.get(key) != b.get(key)]
    metadata = _json_differences({k: v for k, v in reference["twin"].items() if k != "objects"},
                                 {k: v for k, v in candidate["twin"].items() if k != "objects"})
    return {"settings": settings, "primitives": primitives, "twin_metadata": metadata}


def build_case_plan(root: Path, body: CaseProposal | dict) -> dict:
    proposal = body if isinstance(body, CaseProposal) else CaseProposal.model_validate(body)
    recipe = RecipeStore(root).get(proposal.recipe_id)
    try:
        base = SamVolumeRequest.model_validate(recipe["request"]).model_dump(mode="json", exclude_none=True)
    except ValueError as exc:
        raise ValueError(f"This recipe remains readable, but current acquisition validation rejected it: {exc}") from exc
    if proposal.field == "depth_samples" and base["acquisition"]["path_model"] == "continuous_columns_v1":
        raise ValueError("Depth samples are inactive for continuous paths; choose an active sweep parameter.")
    if proposal.field == "defect":
        stack = next((item for item in base["twin"].get("hbm_assemblies", []) if item["id"] == proposal.assembly_id), None)
        micro = stack.get("microstructure") if stack else None
        if not micro or not micro["enabled"] or not stack["physical_present"]:
            raise ValueError("An isolated defect pair requires a present HBM site with an enabled microstructure patch.")
        if not any(item["id"] == proposal.defect_id for item in micro["defects"]):
            raise ValueError("Selected defect_id does not identify an authored local defect.")
        if not base["acquisition"]["include_defects"]:
            raise ValueError("An isolated defect pair requires include_defects=true so the selected defect participates.")
    cases = []
    errors = []
    for index, value in enumerate(proposal.values):
        request = deepcopy(base)
        label = f"{proposal.field} = {value}"
        overrides = {proposal.field: value}
        try:
            if proposal.field == "defect":
                defects = deepcopy(micro["defects"])
                for defect in defects:
                    if defect["id"] == proposal.defect_id:
                        defect["enabled"] = value
                request["twin"] = compose_hbm(request["twin"], proposal.assembly_id, {"microstructure": {"defects": defects}})
                label = f"{proposal.assembly_id} / {proposal.defect_id} / {'enabled' if value else 'disabled'}"
                overrides = {"assembly_id": proposal.assembly_id, "defect_id": proposal.defect_id, "enabled": value}
            else:
                request["acquisition"][proposal.field] = value
                if proposal.field == "include_defects":
                    label = f"All authored defects / {'included' if value else 'excluded'}"
            validated = SamVolumeRequest.model_validate(request)
            estimate = estimate_sam(validated)
            normalized = validated.model_dump(mode="json", exclude_none=True)
            cases.append({"label": label, "overrides": overrides, "request": normalized, "estimate": estimate,
                          "request_sha256": json_sha256(normalized), "geometry_sha256": json_sha256(normalized["twin"])})
        except ValueError as exc:
            errors.append(f"Case {index + 1} ({label}): {exc}")
    if errors:
        raise ValueError("No cases were admitted. " + " | ".join(errors))
    for case in cases:
        case["differences"] = case_differences(cases[0]["request"], case["request"])
    return {"recipe": recipe, "cases": cases, "comparison_basis": "first case",
            "proposal": proposal.model_dump(mode="json", exclude_none=True),
            "recipe_normalization_differences": _json_differences(recipe["request"], base),
            "total_bytes": sum(case["estimate"]["total_bytes"] for case in cases),
            "estimated_peak_bytes": max(case["estimate"]["estimated_peak_bytes"] for case in cases)}
