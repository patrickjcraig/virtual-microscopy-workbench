"""Immutable, self-contained reports for the bounded layered acoustic instrument.

These are standalone calculation reports, never SAM RF datasets or worker jobs.
Historical reads verify frozen content without constructing a twin or rerunning
the current layered kernel. There is no update, resume or delete operation.
"""
from __future__ import annotations

import csv
import hashlib
import io
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
from uuid import uuid4

from .comparisons import _bounded_json, _json_workspace
from .datasets import checked_id, check_disk_space, json_sha256, now_iso


KIND = "layered_acoustic_report"
MAX_REPORT_BYTES = 16*1024**2
MAX_REPORT_EXPANDED_BYTES = 64*1024**2
MAX_NUMERICAL_BYTES = 256*1024**2
MAX_LIST_REPORTS = 1000
IMPLEMENTATION_FILES = (
    "layered_reports.py", "layered_api.py", "layered_analysis.py", "layered_schemas.py",
    "layered_acoustics.py", "layered_time.py", "column_paths.py", "materials.py", "schemas.py", "hbm.py",
    "datasets.py", "comparisons.py", "recipes.py",
)


def bounded_payload(value):
    """Canonical JSON with byte/expansion admission during serialization.

    Iterative encoding avoids retaining an unbounded complete JSON string before
    inspecting its size. Every admitted chunk is counted before accumulation.
    Reads apply the same conservative expansion estimate before JSON decoding.
    """
    encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    output = io.BytesIO()
    size = workspace = 0
    try:
        for token in encoder.iterencode(value):
            if len(token) > MAX_REPORT_BYTES:
                raise ValueError("Layered report exceeds its bounded JSON byte limit.")
            payload = token.encode("utf-8")
            size += len(payload)
            workspace += _json_workspace(payload)
            if size > MAX_REPORT_BYTES:
                raise ValueError("Layered report exceeds its bounded JSON byte limit.")
            if workspace > MAX_REPORT_EXPANDED_BYTES:
                raise ValueError("Layered report exceeds its bounded expanded-provenance workspace.")
            output.write(payload)
    except (TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("Layered report must contain finite, bounded JSON values.") from exc
    return output.getvalue()


def _source_fingerprints():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in IMPLEMENTATION_FILES}


def _numerical_packages(causal):
    packages = {"numpy": version("numpy"), "python": platform.python_version()}
    if causal:
        import flint
        packages.update({"python-flint": version("python-flint"),
            "flint": getattr(flint, "__FLINT_VERSION__", "unavailable"),
            "flint_release": getattr(flint, "__FLINT_RELEASE__", "unavailable")})
    return packages


def _admit_estimate(estimate):
    if not isinstance(estimate, dict):
        raise ValueError("Layered analysis requires a bounded resource estimate.")
    for key, limit in (("estimated_peak_bytes", MAX_NUMERICAL_BYTES), ("estimated_report_bytes", MAX_REPORT_BYTES),
                       ("estimated_report_expanded_bytes", MAX_REPORT_EXPANDED_BYTES)):
        value = estimate.get(key)
        if type(value) is not int or not 0 <= value <= limit:
            raise ValueError(f"Layered analysis {key} exceeds its bounded resource limit or is invalid.")


class LayeredReportStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / "layered-reports"

    def _path(self, identifier):
        identifier = checked_id(identifier)
        if self.directory.is_symlink() or getattr(self.directory, "is_junction", lambda: False)():
            raise ValueError("Layered report storage cannot be a symbolic link or directory junction.")
        target = self.directory / f"{identifier}.json"
        if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
            raise ValueError("Layered reports cannot be symbolic links or directory junctions.")
        if target.resolve().parent != self.directory.resolve() or self.directory.resolve().parent != self.root:
            raise ValueError("Layered report path leaves its data directory.")
        return target

    def create(self, request):
        # Function-local imports keep historical readers independent of current
        # schemas, geometry constructors and numerical implementations.
        from .layered_analysis import analyze_layered, estimate_layered
        from .layered_schemas import LayeredAnalysisRequest

        identifier = str(uuid4())
        path = self._path(identifier)
        if path.exists():
            raise FileExistsError(path)
        request = LayeredAnalysisRequest.model_validate(request)
        normalized = request.model_dump(mode="json")
        request_payload = bounded_payload(normalized)
        estimate = estimate_layered(request)
        _admit_estimate(estimate)
        check_disk_space(self.root, estimate["estimated_report_bytes"])
        analysis = analyze_layered(request)
        if not isinstance(analysis, dict) or not isinstance(analysis.get("provenance", {}), dict):
            raise ValueError("Layered analysis must produce a self-contained result record.")
        report = {**analysis, "schema_version": 1, "kind": KIND, "id": identifier,
            "report_id": identifier, "created_at": now_iso(), "request": normalized,
            "request_sha256": hashlib.sha256(request_payload).hexdigest(), "estimate": estimate,
            "provenance": {**analysis.get("provenance", {}), "implementation_sha256": _source_fingerprints(),
                           "numerical_packages": _numerical_packages(normalized.get("causal_pulse") is not None)}}
        # Bound before hashing too: json_sha256 otherwise serializes a complete
        # unchecked object. The stored checksum excludes only its own field.
        report["report_sha256"] = hashlib.sha256(bounded_payload(report)).hexdigest()
        payload = bounded_payload(report)
        check_disk_space(self.root, len(payload))
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(identifier)
        temporary = self.directory / f".{identifier}.tmp"
        staged = False
        try:
            with temporary.open("xb") as stream:
                staged = True
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # A hard link is an exclusive, atomic publication. It cannot replace
            # an existing report, and readers never see the temporary JSON.
            os.link(temporary, path)
        finally:
            if staged:
                temporary.unlink(missing_ok=True)
        # Return detached JSON data: callers cannot alter a retained analysis
        # object through references exposed in this immutable report response.
        return json.loads(payload)

    def read(self, identifier):
        path = self._path(identifier)
        if not path.is_file():
            raise KeyError(identifier)
        report, _ = _bounded_json(path, MAX_REPORT_BYTES, MAX_REPORT_EXPANDED_BYTES, "Layered acoustic report")
        if (not isinstance(report, dict) or report.get("kind") != KIND or report.get("schema_version") != 1 or
                report.get("id") != identifier or report.get("report_id") != identifier):
            raise ValueError("Layered acoustic report identity is invalid.")
        if report.get("report_sha256") != json_sha256({key: value for key, value in report.items() if key != "report_sha256"}):
            raise ValueError("Layered acoustic report checksum mismatch.")
        if not isinstance(report.get("request"), dict) or report.get("request_sha256") != json_sha256(report["request"]):
            raise ValueError("Layered acoustic request checksum mismatch.")
        return report

    def list(self):
        self._path("00000000-0000-0000-0000-000000000000")
        if not self.directory.exists():
            return []
        reports = []
        for index, path in enumerate(self.directory.glob("*.json")):
            if index >= MAX_LIST_REPORTS:
                raise ValueError("Layered report listing exceeds its bounded 1000-report limit.")
            report = self.read(path.stem)
            estimate = report.get("estimate", {})
            reports.append({"id": report["id"], "report_id": report["report_id"], "kind": KIND,
                "created_at": report["created_at"], "report_sha256": report["report_sha256"],
                "name": report["request"].get("name", "Layered acoustic response"),
                "source_status": report.get("source_status"), "layer_count": estimate.get("layer_count"),
                "frequency_samples": estimate.get("frequency_samples"), "pulse_available": report.get("pulse") is not None,
                **({"causal_pulse_available": True} if report.get("causal_pulse") is not None else {})})
        return sorted(reports, key=lambda item: (item["created_at"], item["id"]), reverse=True)


def layered_report_csv(report):
    """A self-contained JSON cell per report field, preserving exact floats."""
    # CSV quoting can add at most one extra quote per source byte; the input
    # report is already bounded and is checked again for direct function users.
    bounded_payload(report)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["section", "field", "value_json"])
    for key, value in report.items():
        writer.writerow(["report", key, bounded_payload(value).decode("utf-8")])
    return stream.getvalue()
