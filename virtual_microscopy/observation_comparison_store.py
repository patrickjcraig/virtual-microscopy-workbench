"""Immutable observation comparisons with one content-addressed source snapshot.

Historical reads use only bounded JSON and frozen hashes. They do not import an
observation reader, a numerical kernel, FLINT, or a current request schema.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import io
import math
import os
from pathlib import Path
import platform
import stat
from uuid import uuid4

from . import causal_comparison_store as _json

KIND = "sam_observation_comparison"
PROCESSING_VERSION = "observation-comparison-0.16.0"
STORE_CONTRACT = "observation-comparison-report-1"
MAX_SOURCE_MANIFEST_BYTES = 16 * 1024**2
MAX_SOURCE_EXPANDED_BYTES = 64 * 1024**2
MAX_REPORT_BYTES = 64 * 1024**2
MAX_REPORT_EXPANDED_BYTES = 192 * 1024**2
MAX_WORKSPACE_BYTES = 512 * 1024**2
MAX_CATALOG_FILES = 10_000
MAX_PAGE_SIZE = 100
MAX_SUMMARY_BYTES = 64 * 1024
MIN_DISK_RESERVE_BYTES = 64 * 1024**2
CATALOG_ORDER = "id_desc"
BOUND_KEYS = ("complex_source_sum", "magnitude_source_sum", "complex_arithmetic",
              "complex_total", "magnitude_arithmetic", "magnitude_total")
SUMMARY_KEYS = frozenset(("dataset_id", "name", "shape", "manifest_sha256", "input_sha256",
    "completion_sha256", "maximum_complex_bound", "maximum_magnitude_bound", "acquisition",
    "model_version", "operator", "absolute_tolerance", "source_dataset_id",
    "source_manifest_sha256", "solver_sha256"))
IMPLEMENTATION_FILES = ("observation_comparison_store.py", "observation_comparisons.py",
    "observation_comparison_schemas.py", "observation_comparison_math.py",
    "causal_comparison_math.py", "causal_comparisons.py", "causal_comparison_schemas.py", "causal_comparison_store.py",
    "observation_plan.py", "observation_datasets.py", "observation_math.py",
    "observation_schemas.py", "schemas.py", "causal_datasets.py", "datasets.py")


def json_measure(value, byte_limit=None, expanded_limit=None, *, retained_bytes=0):
    """Canonical hash and conservative size, without allocating complete JSON."""
    result = _json.json_measure(value,
        MAX_REPORT_BYTES if byte_limit is None else byte_limit,
        MAX_REPORT_EXPANDED_BYTES if expanded_limit is None else expanded_limit,
        retained_bytes=retained_bytes)
    if retained_bytes + result["expanded_bytes"] + 12*result["encoded_bytes"] + 16*1024**2 > MAX_WORKSPACE_BYTES:
        raise ValueError("Observation comparison exceeds the 512 MiB owned workspace limit.")
    return result


def bounded_payload(value):
    json_measure(value)
    stream = io.BytesIO()
    for part in _json._tokens(value):
        stream.write(part)
    return stream.getvalue()


def bounded_json_read(path, byte_limit=None, expanded_limit=None, *, retained_bytes=0):
    """Source defaults are 16/64 MiB; callers must guard source containment.

    The unchanged shared parser bounds raw bytes/expansion/depth before decoding,
    and rejects linked leaves, duplicate keys, nonfinite values and huge integers.
    """
    return _json.bounded_json_read(path,
        MAX_SOURCE_MANIFEST_BYTES if byte_limit is None else byte_limit,
        MAX_SOURCE_EXPANDED_BYTES if expanded_limit is None else expanded_limit,
        retained_bytes=retained_bytes)


def _fingerprints():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in IMPLEMENTATION_FILES}


def _runtime():
    return {"numerical_packages": {name: version(name) for name in ("numpy", "zarr")},
        "python": platform.python_version(), "implementation": platform.python_implementation(),
        "system": platform.system(), "machine": platform.machine()}


def _disk(root, required):
    import shutil
    probe = root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if shutil.disk_usage(probe).free < required + MIN_DISK_RESERVE_BYTES:
        raise ValueError("Insufficient disk space for the observation comparison and reserve.")


def _digest(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _number(value, *, nonnegative=False):
    return type(value) in (int, float) and math.isfinite(value) and (not nonnegative or value >= 0)


def _shape(value):
    return (type(value) is list and len(value) == 3 and all(type(n) is int for n in value)
        and 14 <= value[0] <= 62 and 14 <= value[1] <= 62 and 2 <= value[2] <= 2049
        and math.prod(value) <= 3_000_000)


def _lightweight(summary):
    if type(summary) is not dict or set(summary) != SUMMARY_KEYS:
        raise ValueError("Observation comparison requires a lightweight source summary, without embedded manifests.")
    for key in ("dataset_id", "source_dataset_id"):
        _json._checked_id(summary[key])
    for key in ("manifest_sha256", "input_sha256", "completion_sha256", "source_manifest_sha256", "solver_sha256"):
        if not _digest(summary[key]):
            raise ValueError("Observation comparison source summary requires canonical SHA-256 identities.")
    if not _shape(summary["shape"]):
        raise ValueError("Observation comparison source summary has an unsupported shape.")
    for key in ("name", "model_version", "operator"):
        if type(summary[key]) is not str or len(summary[key]) > 1024:
            raise ValueError("Observation comparison source labels must be bounded strings.")
    for key in ("maximum_complex_bound", "maximum_magnitude_bound", "absolute_tolerance"):
        if not _number(summary[key], nonnegative=True):
            raise ValueError("Observation comparison source bounds must be finite and nonnegative.")
    acquisition = summary["acquisition"]
    if type(acquisition) is not dict or len(acquisition) > 64:
        raise ValueError("Observation comparison acquisition summary must be a flat bounded record.")
    for key, value in acquisition.items():
        if key == "roi_mm" and value is not None:
            if type(value) is not list or len(value) != 4 or not all(_number(v) for v in value):
                raise ValueError("Observation comparison acquisition ROI must have four finite values.")
        elif type(value) not in (str, int, float, bool, type(None)):
            raise ValueError("Observation comparison acquisition summary cannot embed provenance.")
    json_measure(summary, MAX_SUMMARY_BYTES, 1024**2)


def _no_duplicate_snapshots(report):
    # Walk without retaining every descendant. This follows full JSON admission,
    # which has already rejected cycles and excessive depth or object counts.
    frames = [iter(value for key, value in report.items() if key != "source_snapshots")]
    while frames:
        try:
            value = next(frames[-1])
        except StopIteration:
            frames.pop()
            continue
        if type(value) is dict:
            if ("source_snapshots" in value or "source_manifest" in value or
                    value.get("kind") in ("sam_coherent_observation_volume", "sam_causal_rf_volume") or
                    {"completed_chunks", "input_sha256"} <= value.keys()):
                raise ValueError("Complete source manifests belong only in the top-level snapshot table.")
            frames.append(iter(value.values()))
        elif type(value) is list:
            frames.append(iter(value))


def _validate_content(report, *, historical=False):
    if (type(report) is not dict or report.get("kind") != KIND or
            type(report.get("schema_version")) is not int or report["schema_version"] != 1 or
            type(report.get("processing_version")) is not str or
            not 1 <= len(report["processing_version"]) <= 128 or
            (not historical and report["processing_version"] != PROCESSING_VERSION)):
        raise ValueError("Unsupported observation comparison identity or processing contract.")
    for key in ("request", "source_snapshots", "source_reference", "source_candidate", "source_summaries",
                "compatibility", "metrics", "gate", "bounds", "gate_maps", "initial_view", "resource_estimate"):
        if type(report.get(key)) is not dict:
            raise ValueError(f"Observation comparison requires its frozen {key} record.")
    if not _shape(report.get("shape")) or report.get("axis_order") != ["y", "x", "time"]:
        raise ValueError("Observation comparison has unsupported dimensions or axes.")
    snapshots = report["source_snapshots"]
    if not 1 <= len(snapshots) <= 2:
        raise ValueError("Observation comparison requires one or two deduplicated source snapshots.")
    identifiers = set()
    for digest, source in snapshots.items():
        if (not _digest(digest) or type(source) is not dict or
                source.get("kind") != "sam_coherent_observation_volume" or
                source.get("complete") is not True or source.get("state") != "completed"):
            raise ValueError("Observation comparison snapshots must be frozen complete observation volumes.")
        identifier = _json._checked_id(source.get("dataset_id"))
        if identifier in identifiers:
            raise ValueError("One source dataset cannot have multiple frozen identities in a comparison.")
        identifiers.add(identifier)
        if source.get("shape") != report["shape"]:
            raise ValueError("Observation comparison snapshot dimensions disagree with the report.")
        if json_measure(source, MAX_SOURCE_MANIFEST_BYTES, MAX_SOURCE_EXPANDED_BYTES)["sha256"] != digest:
            raise ValueError("Observation comparison frozen source snapshot checksum mismatch.")
    referenced = set()
    aliases = report["source_summaries"]
    view = report["initial_view"]
    if set(aliases) != {"reference", "candidate"} or view.get("source_summaries") != aliases:
        raise ValueError("Observation comparison source-summary aliases disagree.")
    for role in ("reference", "candidate"):
        summary = report[f"source_{role}"]
        _lightweight(summary)
        digest = summary["manifest_sha256"]
        if digest not in snapshots:
            raise ValueError("Observation comparison source reference has no frozen snapshot.")
        source = snapshots[digest]
        if (summary != aliases[role] or view.get(f"source_{role}") != summary or
                summary["dataset_id"] != source["dataset_id"] or
                report["request"].get(f"{role}_dataset_id") != source["dataset_id"]):
            raise ValueError("Observation comparison source references disagree with their frozen identities.")
        for key in ("shape", "input_sha256", "completion_sha256", "maximum_complex_bound", "maximum_magnitude_bound"):
            if summary[key] != source.get(key):
                raise ValueError(f"Observation comparison source summary {key} disagrees with its snapshot.")
        try:
            plan, request, solver = source["estimate"], source["request"], source["solver"]
            expected = {"name": request.get("name") or plan["source_summary"]["name"],
                "acquisition": plan["inherited_excitation"]["acquisition"],
                "model_version": solver["model_version"], "operator": plan["operator"]["model"],
                "absolute_tolerance": request["absolute_tolerance"],
                "source_dataset_id": plan["source_manifest"]["dataset_id"],
                "source_manifest_sha256": plan["source_manifest_sha256"],
                "solver_sha256": json_measure(solver)["sha256"]}
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("Observation comparison source is missing its frozen summary provenance.") from exc
        if any(summary[key] != value for key, value in expected.items()):
            raise ValueError("Observation comparison summary provenance disagrees with its frozen snapshot.")
        referenced.add(digest)
    if referenced != set(snapshots):
        raise ValueError("Observation comparison cannot retain unused source snapshots.")
    _no_duplicate_snapshots(report)
    ny, nx, _ = report["shape"]
    for key in BOUND_KEYS:
        values = report["bounds"].get(key)
        if (type(values) is not list or len(values) != ny or any(type(row) is not list or len(row) != nx
                or any(not _number(v, nonnegative=True) for v in row) for row in values)):
            raise ValueError("Observation comparison requires six finite nonnegative spatial bound maps.")
    peak = report["resource_estimate"].get("estimated_peak_bytes")
    if type(peak) is not int or not 0 < peak <= MAX_WORKSPACE_BYTES:
        raise ValueError("Observation comparison requires an admitted owned workspace estimate.")


def _account(report, measured):
    minimum = measured["expanded_bytes"] + 12*measured["encoded_bytes"] + 16*1024**2
    resources = report["resource_estimate"]
    if resources["estimated_peak_bytes"] < minimum:
        raise ValueError("Observation comparison advertised workspace is below its actual report requirement.")
    advertised = resources.get("estimated_report_expanded_bytes", measured["expanded_bytes"])
    if type(advertised) is not int or advertised < measured["expanded_bytes"]:
        raise ValueError("Observation comparison advertised report expansion is below its actual requirement.")


def _summary(report):
    value = {key: report[key] for key in ("id", "comparison_id", "kind", "created_at", "processing_version", "report_sha256")}
    value.update(name=report["request"].get("name") or "Coherent observation comparison",
        reference_dataset_id=report["source_reference"]["dataset_id"],
        candidate_dataset_id=report["source_candidate"]["dataset_id"],
        source_reference=report["source_reference"], source_candidate=report["source_candidate"],
        source_summaries=report["source_summaries"], shape=report["shape"], gate=report["gate"],
        initial_view_available=True)
    json_measure(value, MAX_SUMMARY_BYTES, 1024**2)
    return value


class ObservationComparisonStore:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.directory = self.root / "observation-comparisons"

    def _path(self, identifier):
        identifier = _json._checked_id(identifier)
        for parent in (self.root, *self.root.parents):
            _json._reject_link(parent)
        _json._reject_link(self.directory)
        path = self.directory / f"{identifier}.json"
        _json._reject_link(path)
        if (self.directory.resolve().parent != self.root.resolve() or
                path.resolve().parent != self.directory.resolve()):
            raise ValueError("Observation comparison path leaves its data directory.")
        return path

    def create(self, request):
        from .observation_comparisons import compute_observation_comparison
        identifier = str(uuid4())
        path = self._path(identifier)
        if path.exists():
            raise FileExistsError(path)
        result = compute_observation_comparison(self.root, request)
        json_measure(result)
        _validate_content(result)
        report = {**result, "id": identifier, "comparison_id": identifier,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "request_sha256": json_measure(result["request"])["sha256"],
            "store_identity": {"contract": STORE_CONTRACT, "implementation_sha256": _fingerprints(), "runtime": _runtime()}}
        report.pop("report_sha256", None)
        report["report_sha256"] = json_measure(report)["sha256"]
        measured = json_measure(report)
        _account(report, measured)
        _summary(report)
        _disk(self.root, measured["encoded_bytes"])
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(identifier)
        temporary = self.directory / f".{identifier}.tmp"
        staged = False
        try:
            with temporary.open("xb") as stream:
                staged = True
                for part in _json._tokens(report):
                    stream.write(part)
                stream.flush()
                os.fsync(stream.fileno())
            self._path(identifier)
            os.link(temporary, path)
        finally:
            if staged:
                temporary.unlink(missing_ok=True)
        return report

    def read(self, identifier):
        path = self._path(identifier)
        if not path.exists():
            raise KeyError(identifier)
        report, _ = bounded_json_read(path, MAX_REPORT_BYTES, MAX_REPORT_EXPANDED_BYTES)
        measured = json_measure(report)
        _validate_content(report, historical=True)
        _account(report, measured)
        if report.get("id") != identifier or report.get("comparison_id") != identifier:
            raise ValueError("Observation comparison report identity mismatch.")
        expected = json_measure({key: value for key, value in report.items() if key != "report_sha256"})["sha256"]
        if report.get("report_sha256") != expected:
            raise ValueError("Observation comparison report checksum mismatch.")
        if report.get("request_sha256") != json_measure(report["request"])["sha256"]:
            raise ValueError("Observation comparison request checksum mismatch.")
        if type(report.get("store_identity")) is not dict or report["store_identity"].get("contract") != STORE_CONTRACT:
            raise ValueError("Observation comparison frozen store contract is unsupported.")
        return report

    def list(self, limit=50, offset=0):
        if (type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE or
                type(offset) is not int or not 0 <= offset < MAX_CATALOG_FILES):
            raise ValueError("Observation comparison pagination requires limit 1..100 and offset 0..9999.")
        self._path("00000000-0000-0000-0000-000000000000")
        if not self.directory.exists():
            return []
        identifiers = []
        with os.scandir(self.directory) as entries:
            for count, entry in enumerate(entries, start=1):
                if count > MAX_CATALOG_FILES:
                    raise ValueError("Observation comparison catalog exceeds its bounded entry limit.")
                if entry.name.startswith(".") and entry.name.endswith(".tmp"):
                    continue
                if not entry.name.endswith(".json"):
                    raise ValueError("Unexpected entry in observation comparison catalog.")
                identifier = _json._checked_id(entry.name[:-5])
                path = self._path(identifier)
                if not stat.S_ISREG(path.stat().st_mode):
                    raise ValueError("Observation comparison catalog requires regular report files.")
                identifiers.append(identifier)
        identifiers.sort(reverse=True)
        items = []
        retained = 0
        for identifier in identifiers[offset:offset+limit]:
            report = self.read(identifier)
            item = _summary(report)
            retained += json_measure(item)["expanded_bytes"]
            if retained > 8*1024**2:
                raise ValueError("Observation comparison catalog page exceeds its bounded workspace.")
            items.append(item)
            del report
        return items

    def view(self, identifier, **kwargs):
        report = self.read(identifier)
        if not any(value is not None for value in kwargs.values()):
            return report["initial_view"]
        from .observation_comparisons import observation_comparison_view
        return observation_comparison_view(self.root, report, **kwargs)


def observation_comparison_csv(report):
    """Lossless top-level JSON cells, encoded incrementally with ASCII quoting."""
    if type(report) is not dict or any(not key.isascii() for key in report if type(key) is str):
        raise ValueError("Observation comparison CSV requires ASCII top-level field names.")
    json_measure(report)
    stream = io.BytesIO()
    stream.write(b"section,field,value_json\r\n")
    for key in sorted(report):
        stream.write(b'"report","')
        stream.write(key.encode("ascii").replace(b'"', b'""'))
        stream.write(b'","')
        for part in _json._tokens(report[key]):
            stream.write(part.replace(b'"', b'""'))
        stream.write(b'"\r\n')
    return stream.getvalue().decode("ascii")
