"""Bounded, immutable causal-comparison reports, independent of forward solvers.

The complete JSON report is the publication record. A temporary file is fsynced
and hard-linked exclusively, so a failed publication never replaces an existing
report. Catalog pagination is by descending canonical UUID, not creation time.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import io
import json
import math
import os
from pathlib import Path
import platform
import stat
from uuid import UUID, uuid4


KIND = "sam_causal_comparison"
PROCESSING_VERSION = "causal-comparison-0.14.0"
MAX_SOURCE_MANIFEST_BYTES = 8 * 1024**2
MAX_SOURCE_EXPANDED_BYTES = 32 * 1024**2
MAX_REPORT_BYTES = 64 * 1024**2
MAX_REPORT_EXPANDED_BYTES = 192 * 1024**2
MAX_WORKSPACE_BYTES = 512 * 1024**2
MAX_JSON_DEPTH = 64
MAX_CATALOG_FILES = 10_000
MAX_PAGE_SIZE = 100
MAX_SUMMARY_BYTES = 64 * 1024
MIN_DISK_RESERVE_BYTES = 64 * 1024**2
CATALOG_ORDER = "id_desc"
IMPLEMENTATION_FILES = ("causal_comparison_store.py", "causal_comparisons.py",
    "causal_comparison_schemas.py", "causal_comparison_math.py",
    "causal_datasets.py", "datasets.py")


def _checked_id(identifier):
    try:
        if type(identifier) is not str or str(UUID(identifier)) != identifier:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Causal comparison ID must be a canonical UUID.") from exc
    return identifier


def _workspace(payload):
    # Includes punctuation inside strings: deliberate conservative overcounting.
    return len(payload)*8 + 256*sum(payload.count(c) for c in (b"{", b"[", b",", b":"))


def _admit_workspace(expanded, encoded, retained_bytes=0):
    # Covers the retained report, encoder/CSV quoting scratch, complete byte and
    # string exports, and the HTTP caller's UTF-8 conversion. It is an estimate
    # of owned workspace, not total process RSS. Encoding uses ASCII exclusively.
    if (type(retained_bytes) is not int or retained_bytes < 0 or
            retained_bytes + expanded + 12*encoded + 16*1024**2 > MAX_WORKSPACE_BYTES):
        raise ValueError("Causal comparison exceeds the 512 MiB owned workspace limit.")


def _check_tree(value):
    """Check types/depth before the recursive JSON encoder; do not copy arrays."""
    active = set()
    frames = [(iter((value,)), None)]
    while frames:
        iterator, owner = frames[-1]
        try:
            item = next(iterator)
        except StopIteration:
            frames.pop()
            if owner is not None:
                active.remove(owner)
            continue
        if type(item) in (dict, list):
            if id(item) in active or len(frames) > MAX_JSON_DEPTH:
                raise ValueError("Causal comparison JSON is cyclic or exceeds its depth limit.")
            if len(item) > MAX_REPORT_EXPANDED_BYTES//256:
                raise ValueError("Causal comparison JSON container exceeds its expanded limit.")
            active.add(id(item))
            if type(item) is dict:
                for key in item:
                    if type(key) is not str or len(key) > MAX_REPORT_BYTES//8:
                        raise ValueError("Causal comparison JSON requires bounded string keys.")
                children = iter(item.values())
            else:
                children = iter(item)
            frames.append((children, id(item)))
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError("Causal comparison JSON requires finite values.")
        elif type(item) is int:
            if item.bit_length() > 1024:
                raise ValueError("Causal comparison JSON integer exceeds its supported limit.")
        elif type(item) is str:
            if len(item) > MAX_REPORT_BYTES//8:
                raise ValueError("Causal comparison JSON string exceeds its bounded limit.")
        elif item is not None and type(item) is not bool:
            raise ValueError("Causal comparison requires plain finite JSON values.")


def _tokens(value):
    encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, sort_keys=True,
                              separators=(",", ":"))
    try:
        for token in encoder.iterencode(value):
            yield token.encode("ascii")
    except (TypeError, RecursionError, OverflowError, UnicodeError) as exc:
        raise ValueError("Causal comparison requires finite bounded JSON.") from exc


def json_measure(value, byte_limit=None, expanded_limit=None, *, retained_bytes=0):
    """Admit and hash canonical JSON without allocating a complete encoding."""
    byte_limit = MAX_REPORT_BYTES if byte_limit is None else byte_limit
    expanded_limit = MAX_REPORT_EXPANDED_BYTES if expanded_limit is None else expanded_limit
    _check_tree(value)
    encoded = expanded = 0
    digest = hashlib.sha256()
    for part in _tokens(value):
        encoded += len(part)
        expanded += _workspace(part)
        if encoded > byte_limit or expanded > expanded_limit:
            raise ValueError("Causal comparison exceeds its bounded JSON/expanded-memory limit.")
        _admit_workspace(expanded, encoded, retained_bytes)
        digest.update(part)
    return {"encoded_bytes": encoded, "expanded_bytes": expanded, "sha256": digest.hexdigest()}


def bounded_payload(value):
    """Canonical ASCII JSON, admitted before allocating the complete payload."""
    json_measure(value)
    stream = io.BytesIO()
    for part in _tokens(value):
        stream.write(part)
    return stream.getvalue()


def _raw_depth(payload):
    depth = 0
    quoted = escaped = False
    for byte in payload:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("Causal comparison JSON exceeds its depth limit.")
        elif byte in (93, 125):
            depth -= 1


def _reject_link(path):
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise ValueError("Causal comparison reads do not follow symbolic links or junctions.")


def bounded_json_read(path, byte_limit=None, expanded_limit=None, *, retained_bytes=0):
    """Read bounded source/report JSON, rejecting duplicate/nonfinite members.

    The caller owns source-directory containment; this helper additionally rejects
    a linked/nonregular leaf before opening it. All decode admission precedes
    json.loads, including container expansion and nesting depth.
    """
    byte_limit = MAX_SOURCE_MANIFEST_BYTES if byte_limit is None else byte_limit
    expanded_limit = MAX_SOURCE_EXPANDED_BYTES if expanded_limit is None else expanded_limit
    path = Path(path)
    _reject_link(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Causal comparison JSON must be a regular file.")
    if info.st_size > byte_limit:
        raise ValueError("Causal comparison exceeds its bounded JSON byte limit.")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("Causal comparison JSON changed while it was opened.")
        payload = stream.read(byte_limit+1)
    expanded = _workspace(payload)
    if len(payload) > byte_limit or expanded > expanded_limit:
        raise ValueError("Causal comparison exceeds its bounded JSON/expanded-memory limit.")
    _admit_workspace(expanded, len(payload), retained_bytes)
    _raw_depth(payload)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate causal comparison JSON key.")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("Nonfinite causal comparison JSON value.")

    def floating(value):
        result = float(value)
        if not math.isfinite(result):
            constant(value)
        return result

    def integer(value):
        if len(value) > 310:
            raise ValueError("Causal comparison JSON integer exceeds its supported limit.")
        return int(value)

    try:
        report = json.loads(payload, object_pairs_hook=pairs, parse_constant=constant,
                            parse_float=floating, parse_int=integer)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Causal comparison requires finite bounded JSON.") from exc
    return report, expanded


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
        raise ValueError("Insufficient free disk space for the causal comparison and reserve.")


def _validate_content(report, *, historical=False):
    if (type(report) is not dict or report.get("kind") != KIND or report.get("schema_version") != 1 or
            type(report.get("processing_version")) is not str or
            not 1 <= len(report["processing_version"]) <= 128 or
            (not historical and report["processing_version"] != PROCESSING_VERSION)):
        raise ValueError("Unsupported causal comparison report identity or processing contract.")
    for key in ("request", "sources", "source_summaries", "compatibility", "metrics", "gate",
                "bounds", "gate_maps", "initial_view", "resource_estimate"):
        if type(report.get(key)) is not dict:
            raise ValueError(f"Causal comparison requires its frozen {key} record.")
    if set(report["sources"]) != {"reference", "candidate"}:
        raise ValueError("Causal comparison requires both complete frozen source manifests.")
    for role, source in report["sources"].items():
        if (type(source) is not dict or source.get("kind") != "sam_causal_rf_volume" or
                source.get("complete") is not True or source.get("state") != "completed"):
            raise ValueError("Causal comparison sources must be frozen complete causal volumes.")
        identifier = _checked_id(source.get("dataset_id"))
        if report["request"].get(f"{role}_dataset_id") != identifier:
            raise ValueError("Causal comparison source identity disagrees with its request.")
        json_measure(source, MAX_SOURCE_MANIFEST_BYTES, MAX_SOURCE_EXPANDED_BYTES)
    peak = report["resource_estimate"].get("estimated_peak_bytes")
    if type(peak) is not int or not 0 <= peak <= MAX_WORKSPACE_BYTES:
        raise ValueError("Causal comparison requires an admitted owned workspace estimate.")


def _summary(report):
    sources = report["source_summaries"]
    value = {key: report[key] for key in ("id", "comparison_id", "kind", "created_at",
        "processing_version", "report_sha256")}
    value.update(name=report["request"].get("name") or "Causal volume comparison",
        reference_dataset_id=report["request"]["reference_dataset_id"],
        candidate_dataset_id=report["request"]["candidate_dataset_id"],
        source_summaries=sources, shape=report.get("shape"), gate=report["gate"],
        initial_view_available=True)
    json_measure(value, MAX_SUMMARY_BYTES, 1024**2)
    return value


class CausalComparisonStore:
    def __init__(self, root):
        # Preserve the logical path so a configured root link cannot disappear
        # through resolve() before validation.
        self.root = Path(root).absolute()
        self.directory = self.root / "causal-comparisons"

    def _path(self, identifier):
        identifier = _checked_id(identifier)
        for parent in (self.root, *self.root.parents):
            _reject_link(parent)
        _reject_link(self.directory)
        path = self.directory / f"{identifier}.json"
        _reject_link(path)
        if (self.directory.resolve().parent != self.root.resolve() or
                path.resolve().parent != self.directory.resolve()):
            raise ValueError("Causal comparison path leaves its data directory.")
        return path

    def create(self, request):
        # Importing or reading a historical report never imports this module.
        from .causal_comparisons import compute_causal_comparison

        identifier = str(uuid4())
        path = self._path(identifier)
        if path.exists():
            raise FileExistsError(path)
        result = compute_causal_comparison(self.root, request)
        _validate_content(result)
        # Admission precedes hashing/serialization; only a shallow top-level copy
        # is needed. The store retains no mutable report object after returning.
        json_measure(result)
        report = {**result, "id": identifier, "comparison_id": identifier,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "request_sha256": json_measure(result["request"])["sha256"],
            "source_manifest_sha256": {role: json_measure(source)["sha256"]
                for role, source in result["sources"].items()},
            "store_identity": {"contract": "causal-comparison-report-1",
                "implementation_sha256": _fingerprints(), "runtime": _runtime()}}
        report.pop("report_sha256", None)
        report["report_sha256"] = json_measure(report)["sha256"]
        measured = json_measure(report)
        _summary(report)  # Every published report must remain catalog-readable.
        _disk(self.root, measured["encoded_bytes"])
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(identifier)
        temporary = self.directory / f".{identifier}.tmp"
        staged = False
        try:
            with temporary.open("xb") as stream:
                staged = True
                for part in _tokens(report):
                    stream.write(part)
                stream.flush()
                os.fsync(stream.fileno())
            # Guard again immediately before the exclusive publication.
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
        _validate_content(report, historical=True)
        if report.get("id") != identifier or report.get("comparison_id") != identifier:
            raise ValueError("Causal comparison report identity mismatch.")
        expected = report.get("report_sha256")
        if expected != json_measure({key: value for key, value in report.items()
                                     if key != "report_sha256"})["sha256"]:
            raise ValueError("Causal comparison report checksum mismatch.")
        if report.get("request_sha256") != json_measure(report["request"])["sha256"]:
            raise ValueError("Causal comparison request checksum mismatch.")
        expected_sources = {role: json_measure(source)["sha256"] for role, source in report["sources"].items()}
        if report.get("source_manifest_sha256") != expected_sources:
            raise ValueError("Causal comparison frozen source checksum mismatch.")
        if (type(report.get("store_identity")) is not dict or
                report["store_identity"].get("contract") != "causal-comparison-report-1"):
            raise ValueError("Causal comparison frozen store contract is unsupported.")
        return report

    def list(self, limit=50, offset=0):
        if (type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE or
                type(offset) is not int or not 0 <= offset < MAX_CATALOG_FILES):
            raise ValueError("Causal comparison pagination requires limit 1..100 and offset 0..9999.")
        self._path("00000000-0000-0000-0000-000000000000")
        if not self.directory.exists():
            return []
        identifiers = []
        with os.scandir(self.directory) as entries:
            for count, entry in enumerate(entries, start=1):
                if count > MAX_CATALOG_FILES:
                    raise ValueError("Causal comparison catalog exceeds its bounded entry limit.")
                # Incomplete publication files are never catalog records. They
                # remain untouched; crash recovery must not delete unknown data.
                if entry.name.startswith(".") and entry.name.endswith(".tmp"):
                    continue
                if not entry.name.endswith(".json"):
                    raise ValueError("Unexpected entry in causal comparison catalog.")
                identifier = _checked_id(entry.name[:-5])
                path = self._path(identifier)
                if not stat.S_ISREG(path.stat().st_mode):
                    raise ValueError("Causal comparison catalog requires regular report files.")
                identifiers.append(identifier)
        identifiers.sort(reverse=True)
        items = []
        retained = 0
        for identifier in identifiers[offset:offset+limit]:
            report = self.read(identifier)
            item = _summary(report)
            retained += json_measure(item)["expanded_bytes"]
            if retained > 8*1024**2:
                raise ValueError("Causal comparison catalog page exceeds its bounded workspace.")
            items.append(item)
            del report
        return items

    def view(self, identifier, **kwargs):
        report = self.read(identifier)
        if not any(value is not None for value in kwargs.values()):
            return report["initial_view"]
        from .causal_comparisons import causal_comparison_view
        return causal_comparison_view(self.root, report, **kwargs)


def causal_comparison_csv(report):
    """Lossless JSON cell per top-level field, including provenance and maps.

    JSON tokens are ASCII and quoted incrementally; this avoids a complete
    escaped copy of either large source manifest or the complete report string.
    """
    if type(report) is not dict or any(not key.isascii() for key in report if type(key) is str):
        raise ValueError("Causal comparison CSV requires ASCII top-level field names.")
    json_measure(report)
    stream = io.BytesIO()
    stream.write(b"section,field,value_json\r\n")
    for key in sorted(report):
        stream.write(b'"report","')
        stream.write(key.encode("ascii").replace(b'"', b'""'))
        stream.write(b'","')
        for part in _tokens(report[key]):
            stream.write(part.replace(b'"', b'""'))
        stream.write(b'"\r\n')
    return stream.getvalue().decode("ascii")
