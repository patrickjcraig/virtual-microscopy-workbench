"""Deliver exactly three reports from the two frozen v0.15 HBM observations.

Run only after coordinated backend freeze and native QA, never speculatively:
  python -m tools.verify_observation_comparison_delivery artifacts/v016-observation-comparison-delivery

No acquisitions, filtering jobs, forward kernels or output overwrites. Whole
arrays are intentionally retained only for these two pinned QA fixtures. The
production comparison remains row streamed. Fraction oracles certify represented
subtraction arithmetic; direct metrics and gates remain ordinary diagnostics.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import csv
from fractions import Fraction
import hashlib
import io
import json
import math
from pathlib import Path
import platform
import struct
from time import perf_counter
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import numpy as np

from tools.verify_acquisition_comparisons import API, source_hashes, verify_archive, write_bytes, write_json
from tools.verify_causal_comparison_delivery import (assert_exact, check_view as check_signal_view,
    close, controlled_pair_identity, directory_hashes, metric_oracle)
from tools.verify_layered_acoustics import legacy_deliveries
from tools.verify_observation_delivery import (observation_catalog, read_observation, read_source,
    spatial_contract, effect_support, IDS as PARENT_IDS)
from virtual_microscopy.datasets import canonical_json, now_iso


PREFIX = "/api/v2/observation-comparisons"
SIGNALS = ("rf", "imaginary", "envelope")
BOUND_KEYS = ("complex_source_sum", "magnitude_source_sum", "complex_arithmetic",
              "complex_total", "magnitude_arithmetic", "magnitude_total")
IDS = {"hbm6-intact":"969e51c3-6938-4786-b5fb-49cd5034de2f",
       "hbm6-missing-bump":"185aa9df-3075-472d-a5b1-7a8a65bb734d"}
SHAPE = [62,30,1601]
HELPERS = ("verify_observation_comparison_delivery.py", "verify_observation_delivery.py",
    "verify_causal_comparison_delivery.py", "verify_acquisition_comparisons.py", "verify_layered_acoustics.py",
    "build_hbm_microstructure_example.py", "build_h100_example.py")


def exact(value):
    return Fraction(float(value))


def upward(value):
    result = float(value)
    if exact(result) < value:
        result = math.nextafter(result, math.inf)
    assert math.isfinite(result) and exact(result) >= value
    assert result == 0 or exact(math.nextafter(result, -math.inf)) < value
    return result


def catalog(api):
    records, offset = [], 0
    while True:
        page = api.json(f"{PREFIX}?limit=100&offset={offset}")
        assert page["order"] == "id_desc"
        records.extend(page["comparisons"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    identifiers = [r["id"] for r in records]
    assert identifiers == sorted(set(identifiers), reverse=True)
    return records


def pair_groups(a, b):
    """Exact waveform and bound bytes define equivalence, never local class IDs."""
    groups = {}
    for y in range(a["rf"].shape[0]):
        for x in range(a["rf"].shape[1]):
            signature = tuple(
                tuple(hashlib.sha256(source[key][y,x].tobytes()).hexdigest() for key in SIGNALS)+
                tuple(struct.pack("<d", source[key][y,x]).hex() for key in ("complex_total","magnitude_total"))
                for source in (a,b))
            groups.setdefault(signature, []).append((y,x))
    return groups


def fraction_oracle(report, a, b):
    groups = pair_groups(a,b)
    u, eta = Fraction(1,2**53), Fraction(1,2**1074)
    records = []
    for columns in groups.values():
        y,x = columns[0]
        sources = {prefix:exact(a[key][y,x])+exact(b[key][y,x])
                   for prefix,key in (("complex","complex_total"),("magnitude","magnitude_total"))}
        rho = {key:u*(max(exact(abs(v)) for v in a[key][y,x])+
                         max(exact(abs(v)) for v in b[key][y,x]))+eta for key in SIGNALS}
        expected = {"complex_source_sum":upward(sources["complex"]),
                    "magnitude_source_sum":upward(sources["magnitude"]),
                    "complex_arithmetic":upward(rho["rf"]+rho["imaginary"]),
                    "magnitude_arithmetic":upward(rho["envelope"])}
        for prefix in ("complex","magnitude"):
            expected[prefix+"_total"] = upward(exact(expected[prefix+"_source_sum"])+exact(expected[prefix+"_arithmetic"]))
        errors = {}
        for key in SIGNALS:
            av,bv = a[key][y,x],b[key][y,x]
            residual = bv-av
            errors[key] = [abs(exact(d)-(exact(right)-exact(left))) for left,right,d in zip(av,bv,residual)]
            assert all(error <= rho[key] for error in errors[key])
        complex_error = max(left+right for left,right in zip(errors["rf"],errors["imaginary"]))
        magnitude_error = max(errors["envelope"])
        assert complex_error <= exact(expected["complex_arithmetic"])
        assert magnitude_error <= exact(expected["magnitude_arithmetic"])
        assert sources["complex"]+complex_error <= exact(expected["complex_total"])
        assert sources["magnitude"]+magnitude_error <= exact(expected["magnitude_total"])
        for yy,xx in columns:
            for values in (a,b):
                for key in SIGNALS:
                    assert values[key][yy,xx].tobytes() == values[key][y,x].tobytes()
                for key in ("complex_total","magnitude_total"):
                    assert struct.pack("<d",values[key][yy,xx]) == struct.pack("<d",values[key][y,x])
            for key in BOUND_KEYS:
                assert struct.pack("<d",report["bounds"][key][yy][xx]) == struct.pack("<d",expected[key])
        records.append({"representative_yx":[y,x],"equivalent_columns":len(columns),"time_samples":len(a["time_us"]),
            "bounds":expected,"maximum_actual_complex_l1_subtraction_error":float(complex_error),
            "maximum_actual_saved_magnitude_subtraction_error":float(magnitude_error)})
    return {"columns_covered":a["rf"].shape[0]*a["rf"].shape[1],"distinct_waveform_bound_pairs":len(groups),
        "exact_component_checks":len(groups)*len(a["time_us"])*3,"all_six_maps_exact_minimal":True,
        "equivalent_sources_checked_byte_exact":True,"representatives":records,
        "scope":"Exact represented-input subtraction enclosures plus separate frozen complex and magnitude bounds; no statistical or physical accuracy certificate."}


def check_view(view,a,b,indices,report,manifest):
    check_signal_view(view,a,b,indices)
    x,y,_ = indices
    assert view["cursor"]["source_x_index"] == manifest["estimate"]["source_indices"]["x"][x]
    assert view["cursor"]["source_y_index"] == manifest["estimate"]["source_indices"]["y"][y]
    assert_exact(view["extent_mm"],manifest["estimate"]["extent_mm"])
    for key in BOUND_KEYS:
        assert view["selected_bounds"][key] == report["bounds"][key][y][x]
    assert view["gate_maps"] == report["gate_maps"]


def verify_report(report,ma,mb,a,b):
    assert report["shape"] == SHAPE and report["axis_order"] == ["y","x","time"]
    snapshots = {}
    for role,manifest in (("reference",ma),("candidate",mb)):
        digest = hashlib.sha256(canonical_json(manifest)).hexdigest()
        snapshots[digest] = manifest
        assert report[f"source_{role}"]["manifest_sha256"] == digest
        assert report[f"source_{role}"]["dataset_id"] == manifest["dataset_id"]
        assert "source_manifest" not in report[f"source_{role}"]
    assert report["source_snapshots"] == snapshots
    assert len(snapshots) == (1 if ma["dataset_id"] == mb["dataset_id"] else 2)
    for key in ("x_mm","y_mm","time_us"):
        assert a[key].tobytes() == b[key].tobytes()
        assert_exact(report["coordinates"][key],a[key])
    request = report["request"]
    lo,hi = (int(np.searchsorted(a["time_us"],request[key],side=side))
             for key,side in (("gate_start_us","left"),("gate_end_us","right")))
    assert report["gate"]["start_index"] == lo and report["gate"]["stop_index_exclusive"] == hi
    assert report["gate"]["sample_count"] == hi-lo
    assert report["gate"]["actual_start_us"] == a["time_us"][lo]
    assert report["gate"]["actual_end_us"] == a["time_us"][hi-1]
    metrics = {"full_record":metric_oracle(report["metrics"]["full_record"],a,b,0,len(a["time_us"])),
               "gate":metric_oracle(report["metrics"]["gate"],a,b,lo,hi)}
    for mode in ("peak_envelope","rms_rf"):
        expected = {role:(values["envelope"][:,:,lo:hi].max(axis=2) if mode=="peak_envelope" else
                         np.sqrt(np.mean(values["rf"][:,:,lo:hi]**2,axis=2)))
                    for role,values in (("reference",a),("candidate",b))}
        for role,values in expected.items():
            close(report["gate_maps"][mode][role],values)
        close(report["gate_maps"][mode]["difference"],expected["candidate"]-expected["reference"])
    cursor = tuple(report["initial_view"]["cursor"][key] for key in ("x_index","y_index","time_index"))
    check_view(report["initial_view"],a,b,cursor,report,ma)
    if a is b:
        for key in (*SIGNALS,"complex"):
            assert report["metrics"]["full_record"][key]["rmse"] == 0.
        for key in ("complex_source_sum","magnitude_source_sum"):
            assert np.all(np.asarray(report["bounds"][key]) > 0)
    return {"metrics":metrics,"certificates":fraction_oracle(report,a,b),
        "source_snapshot_count":len(snapshots),"source_snapshots_exact_deduplicated":True,
        "inclusive_gate_and_independent_reductions":True,"initial_view_signal_bytes_exact":True,
        "full_record_maps_independent_of_gate":True}


def historical_exports(api,root,directory,report):
    from virtual_microscopy.observation_comparison_store import (ObservationComparisonStore,
        bounded_payload, observation_comparison_csv)
    identifier = report["id"]
    route = f"{PREFIX}/{identifier}"
    saved = (root/"observation-comparisons"/f"{identifier}.json").read_bytes()
    j,c = api.bytes(route+"/export?format=json"),api.bytes(route+"/export?format=csv")
    assert j == saved == canonical_json(report)
    previous = csv.field_size_limit(64*1024**2)
    try:
        decoded = {}
        for row in csv.DictReader(io.StringIO(c.decode("utf8"))):
            assert row["section"] == "report" and row["field"] not in decoded
            decoded[row["field"]] = json.loads(row["value_json"])
    finally:
        csv.field_size_limit(previous)
    assert canonical_json(decoded) == j
    assert report["report_sha256"] == hashlib.sha256(canonical_json({k:v for k,v in report.items() if k!="report_sha256"})).hexdigest()
    write_bytes(directory/"report-export.json",j)
    write_bytes(directory/"report-export.csv",c)
    offline = directory/"offline-report-only"
    (offline/"observation-comparisons").mkdir(parents=True)
    write_bytes(offline/"observation-comparisons"/f"{identifier}.json",saved)
    with ExitStack() as traps:
        for target in ("virtual_microscopy.observation_comparisons.compute_observation_comparison",
            "virtual_microscopy.observation_comparisons.observation_comparison_view",
            "virtual_microscopy.observation_comparison_store._fingerprints",
            "virtual_microscopy.observation_comparison_store._runtime",
            "virtual_microscopy.observation_datasets.ObservationStore.verify_complete",
            "virtual_microscopy.observation_datasets.observation_identity",
            "virtual_microscopy.observation_math.observe_row",
            "virtual_microscopy.causal_datasets.CausalSamDatasetStore.verify_complete",
            "virtual_microscopy.causal_sam.causal_gamma_response",
            "virtual_microscopy.layered_time.causal_gamma_response"):
            traps.enter_context(patch(target,side_effect=AssertionError("Historical report accessed source or current numerical machinery")))
        store = ObservationComparisonStore(offline)
        assert store.read(identifier) == report and store.view(identifier) == report["initial_view"]
        assert bounded_payload(store.read(identifier)) == j
        assert observation_comparison_csv(store.read(identifier)).encode("utf8") == c
    try:
        store.view(identifier,x_index=0)
        raise AssertionError("A new cursor unexpectedly succeeded without its source observations")
    except KeyError:
        pass
    assert api.json(route) == report and api.json(route+"/view") == report["initial_view"]
    return {"json_bytes":len(j),"csv_bytes":len(c),"json_exact_saved_file":True,
        "csv_lossless_json_cells":True,"source_absent_initial_view_and_exports":True,
        "source_and_current_forward_traps_passed":True,"new_source_absent_cursor_rejected":True}


def reject_controls(api,root,reference,parent):
    before,observations,jobs = catalog(api),observation_catalog(api),api.json("/api/v2/jobs")
    files = directory_hashes(root/"observation-comparisons")
    records = []
    for name,candidate,gate in (("independent_parent_kind",parent,(.32,.4)),
                               ("gate_outside_record",reference,(0.,3.))):
        request = {"reference_dataset_id":reference,"candidate_dataset_id":candidate,
                   "gate_start_us":gate[0],"gate_end_us":gate[1]}
        try:
            with urlopen(Request(api.base+PREFIX,data=canonical_json(request),
                headers={"Content-Type":"application/json"}),timeout=180) as response:
                raise AssertionError(f"Control {name} unexpectedly returned {response.status}")
        except HTTPError as exc:
            assert exc.code == 422
            records.append({"control":name,"request":request,"status":422,"response":json.loads(exc.read())})
    assert catalog(api)==before and observation_catalog(api)==observations and api.json("/api/v2/jobs")==jobs
    assert directory_hashes(root/"observation-comparisons")==files
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    parser.add_argument("--url",default="http://127.0.0.1:8767")
    parser.add_argument("--data-root",type=Path,default=Path("artifacts/volumes-v07"))
    parser.add_argument("--active-instance",type=Path,default=Path("artifacts/ACTIVE_INSTANCE.json"))
    args = parser.parse_args()
    output,root = args.output.resolve(),args.data_root.resolve()
    output.mkdir(parents=True,exist_ok=False)  # Before any network/source read.
    start = perf_counter()
    run = {"schema_version":1,"run_id":str(uuid4()),"created_at":now_iso(),"version":"0.16.0",
        "api_url":args.url,"data_root":str(root),"no_overwrite":True,"source_observation_ids":IDS,
        "helper_source_sha256":{n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in HELPERS},
        "runtime":{"python":platform.python_version(),"numpy":np.__version__,"platform":platform.platform()},
        "evidence_status":"Saved synthetic finite-filter comparison; no propagation, calibrated beam, experimental accuracy or defect-detection claim."}
    write_json(output/"run.json",run)
    try:
        project = Path(__file__).resolve().parents[1]
        active,previous,legacy_ids = legacy_deliveries(args.active_instance.resolve(),project,root)
        assert all(active["delivered_observation_dataset_ids"][key]==value for key,value in IDS.items())
        api = API(args.url)
        health,jobs,observations = api.json("/api/health"),api.json("/api/v2/jobs"),observation_catalog(api)
        assert health["status"]=="ok" and health["version"]=="0.16.0"
        assert not any(j["status"] in {"queued","running","cancelling"} for j in [*jobs["jobs"],*observations])
        before_catalog = catalog(api)
        # Hash every pre-existing data-root directory, including old/native data
        # outside the named delivery set. Catalog semantics are checked via API.
        directories = {p.name:directory_hashes(p) for p in root.iterdir() if p.is_dir()}
        directories.setdefault("observation-comparisons",{})
        assert sum(len(directories[i]) for i in legacy_ids)==524
        assert len(directories["layered-reports"])==19
        for name,value in (("active-instance-snapshot.json",active),("previous-deliveries.json",previous),
            ("health.json",health),("jobs-before.json",jobs),("observation-jobs-before.json",observations),
            ("comparison-catalog-before.json",before_catalog),("all-directory-hashes-before.json",directories)):
            write_json(output/name,value)
        am,a = read_observation(root,IDS["hbm6-intact"])
        bm,b = read_observation(root,IDS["hbm6-missing-bump"])
        pam,pa = read_source(root,PARENT_IDS["hbm6-intact"])
        pbm,pb = read_source(root,PARENT_IDS["hbm6-missing-bump"])
        identity = controlled_pair_identity(pam,pbm,pa,pb)
        spatial = {"reference":spatial_contract(pam,pa,am,a),"candidate":spatial_contract(pbm,pb,bm,b)}
        locality = effect_support(pa,pb,a,b,identity)
        write_json(output/"controlled-pair.json",{"identity":identity,"spatial":spatial,"locality":locality})
        del pa,pb
        # Retain byte-exact raw source ZIPs; no source recomputation occurs.
        for slug,identifier in IDS.items():
            archive = output/f"{slug}-source.zip"
            write_bytes(archive,api.bytes(f"/api/v2/observations/datasets/{identifier}/export"))
            verify_archive(archive,root/identifier,directories[identifier])
        write_json(output/"rejected-controls.json",reject_controls(api,root,IDS["hbm6-intact"],PARENT_IDS["hbm6-intact"]))
        cases = [("hbm6-controlled-defect-gate",am,bm,a,b,.32,.4),
                 ("hbm6-same-source-zero",am,am,a,a,.32,.4),
                 ("hbm6-controlled-defect-full-record",am,bm,a,b,float(a["time_us"][0]),float(a["time_us"][-1]))]
        records,created,full_bounds = [],{},None
        for slug,ma,mb,av,bv,left,right in cases:
            directory = output/slug
            directory.mkdir()
            request = {"name":f"v0.16 {slug}","reference_dataset_id":ma["dataset_id"],"candidate_dataset_id":mb["dataset_id"],
                "policy":"same_observation_and_excitation_v1","gate_start_us":left,"gate_end_us":right,
                "x_index":9,"y_index":42,"time_index":265}
            write_json(directory/"request.json",request)
            submitted = perf_counter()
            report = api.json(PREFIX,request)
            elapsed = perf_counter()-submitted
            created[slug] = report["id"]
            write_json(directory/"report.json",report)
            assert report["request"]==request
            checks = verify_report(report,ma,mb,av,bv)
            exports = historical_exports(api,root,directory,report)
            cursor = (10,43,266)
            view = api.json(f"{PREFIX}/{report['id']}/view?x_index=10&y_index=43&time_index=266")
            check_view(view,av,bv,cursor,report,ma)
            write_json(directory/"changed-cursor.json",view)
            if ma is not mb:
                current = {key:report["bounds"][key] for key in BOUND_KEYS}
                if full_bounds is None:
                    full_bounds = current
                else:
                    assert current==full_bounds
            record = {"experiment":slug,"report_id":report["id"],"shape":report["shape"],
                "source_ids":[ma["dataset_id"],mb["dataset_id"]],"create_wall_seconds":elapsed,
                "gate":report["gate"],"metrics":report["metrics"],"selected_bounds":view["selected_bounds"],
                "maximum_bounds":{key:float(np.max(report["bounds"][key])) for key in BOUND_KEYS},
                "resources":report["resource_estimate"],"checks":checks,"exports":exports}
            write_json(directory/"verification.json",record)
            records.append(record)
            print(json.dumps({"status":"verified","experiment":slug,"report_id":report["id"],"create_wall_seconds":elapsed}),flush=True)
        jobs_after,observations_after,after_catalog = api.json("/api/v2/jobs"),observation_catalog(api),catalog(api)
        assert jobs_after==jobs and observations_after==observations
        assert {v["id"] for v in after_catalog}-{v["id"] for v in before_catalog}==set(created.values())
        assert all(value in after_catalog for value in before_catalog)
        after = {p.name:directory_hashes(p) for p in root.iterdir() if p.is_dir()}
        assert set(after)==set(directories)
        for name,files in directories.items():
            assert all(after[name].get(path)==value for path,value in files.items())
            if name!="observation-comparisons":
                assert after[name]==files
        assert set(after["observation-comparisons"])-set(directories["observation-comparisons"])=={f"{v}.json" for v in created.values()}
        for slug,identifier in created.items():
            assert (root/"observation-comparisons"/f"{identifier}.json").read_bytes()==(output/slug/"report-export.json").read_bytes()
        for name,value in (("jobs-after.json",jobs_after),("observation-jobs-after.json",observations_after),
            ("comparison-catalog-after.json",after_catalog),("all-directory-hashes-after.json",after)):
            write_json(output/name,value)
        final = {**run,"status":"passed","elapsed_seconds":perf_counter()-start,"report_ids":created,"reports":records,
            "controlled_pair":identity,"effect_support":locality,"comparison_reports_created":3,
            "acquisitions_created":0,"observation_jobs_created":0,"both_job_catalogs_unchanged":True,
            "all_preexisting_directory_files_preserved":True,"preserved_files":sum(map(len,directories.values())),
            "legacy_delivery_files_preserved":524,"layered_reports_preserved":19,
            "source_causal_datasets_preserved":sum(j.get("kind")=="sam_causal_rf_volume" for j in jobs["jobs"]),
            "preexisting_observations_preserved":len(observations),"new_report_reads_exports_immutable":True,
            "both_raw_source_zip_exports_byte_exact":True,"rejection_controls_created_no_reports_or_jobs":True}
        write_json(output/"verification-report.json",final)
        print(json.dumps({"status":"passed","output":str(output),"report_ids":created,"elapsed_seconds":final["elapsed_seconds"]}),flush=True)
    except BaseException as exc:
        write_json(output/"failure.json",{"type":type(exc).__name__,"message":str(exc),"elapsed_seconds":perf_counter()-start})
        raise


if __name__=="__main__":
    main()
