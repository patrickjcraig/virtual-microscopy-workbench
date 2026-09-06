"""Independent material-binding, full-depth geometry and bounded-history checks."""
from copy import deepcopy
import math

import numpy as np
import pytest

from virtual_microscopy import material_assignments as core
from virtual_microscopy.material_assignment_schemas import SLSMaterialAssignmentRequest


def primitive(identifier, material, shape="box", center=(2., 1.5, 1.), size=(2., 2., 1.), role="structure"):
    return {"id": identifier, "name": identifier, "material": material, "shape": shape,
            "center_mm": list(center), "size_mm": list(size), "role": role}


def twin():
    return {"name": "Manufactured geometry, arbitrary assumptions", "size_mm": [4., 3., 2.],
            "objects": [primitive("box", "silicon"),
                        primitive("cylinder", "copper", "cylinder", size=(1., 1., 1.5)),
                        primitive("void", "air", "sphere", size=(.5, .5, .5), role="defect")]}


def manual(material="silicon", **parameters):
    values = {"density_kg_m3": 1000., "relaxed_modulus_gpa": 2.25,
              "unrelaxed_modulus_gpa": 4., "relaxation_time_us": .003}
    values.update(parameters)
    return {"material_id": material, "name": "Explicit arbitrary scalar material", "note": "Manufactured test values, not calibrated.",
            "origin": {"kind": "manual", **values}}


def request(**changes):
    value = {"kind": "sls_material_assignment", "name": "Coverage evidence", "twin": twin()}
    value.update(changes)
    return value


def build(**changes):
    return core.build_assignment(request(**changes), {})


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    from virtual_microscopy.sls_reports import SLSReportStore
    medium = {"name": "Assumed water", "impedance_mrayl": 1.48, "sound_speed_m_s": 1480.}
    layer = {"name": "Independent source layer", "thickness_mm": .05,
             **{k: v for k, v in manual()["origin"].items() if k != "kind"}}
    return SLSReportStore(tmp_path_factory.mktemp("assignment-source")).create({
        "name": "Original source context", "stack": {"incident": medium, "terminal": medium, "layers": [layer]},
        "spectrum": {"start_mhz": 0., "end_mhz": 20., "samples": 3}})


def reference(source, material="silicon", index=0):
    result = manual(material)
    result["origin"] = {"kind": "report_layer", "report_id": source["id"], "layer_index": index}
    return result


def test_empty_assignment_freezes_normalized_twin_and_preserves_original():
    r = request(); before = deepcopy(r)
    d = core.build_assignment(r, {})
    assert r == before
    frozen = SLSMaterialAssignmentRequest.model_validate(r).model_dump(mode="json", exclude_none=True)
    assert d["snapshots"][d["twin_sha256"]] == frozen["twin"]
    assert d["input_request_sha256"] == core._sha(frozen)
    assert "twin" not in d["request"] and len(d["snapshots"]) == 1
    assert d["coverage"]["missing_material_ids"] == ["silicon", "copper", "air"]
    assert d["coverage"]["status"] == "incomplete" and not d["propagation_available"]
    r["twin"]["objects"].reverse()
    assert d["snapshots"][d["twin_sha256"]]["objects"][0]["id"] == "box"


def test_selected_scope_completeness_does_not_hide_actual_column_missing_air():
    d = build(coverage_scope="selected_materials", required_material_ids=["copper"], bindings=[manual("copper")])
    assert d["coverage"]["status"] == "complete_for_selected_materials"
    c = core.inspect_column(d, 2., 1.5)
    assert c["coverage"]["required_material_ids"] == ["copper", "air"]
    assert c["coverage"]["missing_material_ids"] == ["air"] and not c["coverage"]["complete"]
    assert any(s["material_id"] == "air" and s["coverage_status"] == "missing" for s in c["segments"])
    assert all(s["assignment_sha256"] is None for s in c["segments"] if s["material_id"] == "air")


def test_explicit_air_binding_is_separate_from_lossless_ambient_policy():
    d = build(bindings=[manual("copper"), manual("air")])
    c = core.inspect_column(d, 2., 1.5)
    assert c["coverage"]["status"] == "complete_for_column"
    assert c["coverage"]["ambient_present"] and not c["propagation_available"]
    for s in c["segments"]:
        if s["material_label"] == 0:
            assert s["material_id"] == "ambient_water" and s["assignment_sha256"] is None
            assert s["coverage_status"] == "ambient_policy"
        else:
            assert s["coverage_status"] == "assigned" and s["assignment_sha256"]
    assert c["ambient_policy"]["impedance_mrayl"] == 1.48
    assert c["ambient_policy"]["sound_speed_m_s"] == 1480.
    assert c["ambient_policy"]["pressure_loss_db_mm"] == 0.


def test_occluded_material_remains_inventory_but_is_absent_from_column():
    value = twin(); value["objects"] = [primitive("hidden", "air", size=(.5, .5, .5)), primitive("cover", "silicon")]
    d = build(twin=value, bindings=[manual()])
    assert d["coverage"]["missing_material_ids"] == ["air"]
    c = core.inspect_column(d, 2., 1.5)
    assert c["coverage"]["complete"] and c["coverage"]["required_material_ids"] == ["silicon"]


@pytest.mark.parametrize("x,defects,ends,materials", [
    (2., True, [.25,.75,1.25,1.75,2.], ["ambient_water","copper","air","copper","ambient_water"]),
    (2.125, True, [.25,1-math.sqrt(3)/8,1+math.sqrt(3)/8,1.75,2.], ["ambient_water","copper","air","copper","ambient_water"]),
    (2.25, True, [.25,1.75,2.], ["ambient_water","copper","ambient_water"]),
    (2.5, True, [.25,1.75,2.], ["ambient_water","copper","ambient_water"]),
    (2.75, True, [.5,1.5,2.], ["ambient_water","silicon","ambient_water"]),
    (3., True, [.5,1.5,2.], ["ambient_water","silicon","ambient_water"]),
    (.5, True, [2.], ["ambient_water"]),
    (2., False, [.25,1.75,2.], ["ambient_water","copper","ambient_water"]),
])
def test_independent_analytical_ordered_box_cylinder_sphere_partitions(x, defects, ends, materials):
    c = core.inspect_column(build(include_defects=defects), x, 1.5)
    assert [s["material_id"] for s in c["segments"]] == materials
    np.testing.assert_allclose([s["z_end_mm"] for s in c["segments"]], ends, rtol=0, atol=1e-15)
    assert c["segments"][0]["z_start_mm"] == 0 and c["segments"][-1]["z_end_mm"] == 2
    assert all(s["thickness_mm"] > 0 for s in c["segments"])
    assert math.fsum(s["thickness_mm"] for s in c["segments"]) == pytest.approx(2.)
    assert c["segment_count"] == len(ends)


def test_frozen_primitive_order_changes_result_without_reordering_original():
    original = twin(); reverse = deepcopy(original); reverse["objects"].reverse()
    a, b = build(twin=original), build(twin=reverse)
    ca, cb = core.inspect_column(a, 2., 1.5), core.inspect_column(b, 2., 1.5)
    assert a["twin_sha256"] != b["twin_sha256"]
    assert [s["material_id"] for s in cb["segments"]] == ["ambient_water","copper","silicon","copper","ambient_water"]
    assert ca["segments"] != cb["segments"] and original["objects"][0]["id"] == "box"


def test_reference_copy_is_exact_and_deduplicates_complete_source_without_transferring_thickness(source):
    before = deepcopy(source)
    d = core.build_assignment(request(bindings=[reference(source), reference(source,"copper")]), {source["id"]:source})
    assert source == before and len(d["snapshots"]) == 2
    hashes = {b["origin"]["source_snapshot_sha256"] for b in d["bindings"]}
    assert len(hashes) == 1
    assert d["snapshots"][next(iter(hashes))] == source
    for b in d["bindings"]:
        assert set(b["parameters"]) == set(core.PARAMETER_KEYS)
        for key in core.PARAMETER_KEYS:
            assert float(b["parameters"][key]).hex() == float(source["stack"]["layers"][0][key]).hex()
        assert "thickness_mm" not in b["parameters"]
        assert b["parameters_sha256"] == core._sha(b["parameters"])
        assert b["assignment_sha256"] == core._sha({k:v for k,v in b.items() if k!="assignment_sha256"})
    source["stack"]["layers"][0]["name"] = "Caller mutation"
    assert d["snapshots"][next(iter(hashes))]["stack"]["layers"][0]["name"] != "Caller mutation"
    source["stack"]["layers"][0]["name"] = before["stack"]["layers"][0]["name"]
    c = core.inspect_column(d, 2., 1.5)
    assert any(s["material_id"] == "copper" and s["thickness_mm"] == .5 for s in c["segments"])


def test_nominal_density_is_a_frozen_difference_not_an_inferred_parameter():
    d = build(bindings=[manual(density_kg_m3=2718.)])
    b = d["bindings"][0]
    assert b["parameters"]["density_kg_m3"] == 2718.
    assert b["nominal_density_kg_m3"] == 2329. and b["nominal_density_difference_kg_m3"] == 389.
    assert b["evidence"] == "manual_scalar_longitudinal_assumption"


def test_source_layer_index_and_checksum_staleness_reject(source):
    with pytest.raises(ValueError, match="no finite layer"):
        core.build_assignment(request(bindings=[reference(source,index=1)]), {source["id"]:source})
    stale = deepcopy(source); stale["stack"]["layers"][0]["density_kg_m3"] = 1001.
    with pytest.raises(ValueError):
        core.build_assignment(request(bindings=[reference(source)]), {source["id"]:stale})


@pytest.mark.parametrize("sources", [{}, {"unused":{}}])
def test_missing_and_unused_source_ids_reject_before_source_access(source,sources):
    with pytest.raises(ValueError, match="exactly match"):
        core.build_assignment(request(bindings=[reference(source)]),sources)


def test_estimate_never_computes_paths_and_matches_frozen_request_roundtrip(monkeypatch):
    monkeypatch.setattr(core,"build_column_paths",lambda *a,**k:pytest.fail("Estimate computed a path"))
    r = request(bindings=[manual()])
    m = SLSMaterialAssignmentRequest.model_validate(r)
    a = core.estimate_assignment(m,{})
    b = core.estimate_assignment(m.model_dump(mode="json",exclude_none=True),{})
    assert a == b and "snapshots" not in a and "snapshot_kinds" not in a
    d = core.build_assignment(r,{})
    assert d["resources"] == a["resources"]
    assert core._measure(d)["encoded_bytes"] < d["resources"]["estimated_report_bytes"]


def test_new_column_never_reconstructs_twin_or_calls_forward_kernel(monkeypatch,source):
    d = core.build_assignment(request(bindings=[reference(source)]),{source["id"]:source})
    from virtual_microscopy.schemas import Twin
    import virtual_microscopy.sls_schemas as schemas
    import virtual_microscopy.column_paths as paths
    forbidden = lambda *a,**k:pytest.fail("A constructor or numerical forward response was used")
    monkeypatch.setattr(Twin,"model_validate",forbidden)
    monkeypatch.setattr(schemas.SLSAnalysisRequest,"model_validate",forbidden)
    monkeypatch.setattr(paths,"column_acoustic_echoes",forbidden)
    monkeypatch.setattr(paths,"column_xray_integrals",forbidden)
    assert core.inspect_column(d,2.,1.5)["segment_count"] == 5


@pytest.mark.parametrize("field,value",[("include_defects",1),("include_defects","true"),("name"," "),
    ("kind","sam_causal_rf_volume"),("coverage_scope","roi"),("required_material_ids",["water"])])
def test_strict_top_level_fields(field,value):
    with pytest.raises(ValueError):build(**{field:value})


def test_kind_is_explicit_and_unknown_extra_fields_reject():
    r=request();del r["kind"]
    with pytest.raises(ValueError):core.build_assignment(r,{})
    with pytest.raises(ValueError):build(frequency_mhz=100.)


@pytest.mark.parametrize("changes",[
    {"coverage_scope":"selected_materials"},
    {"required_material_ids":["silicon"]},
    {"coverage_scope":"selected_materials","required_material_ids":["silicon","silicon"]},
    {"coverage_scope":"selected_materials","required_material_ids":["epoxy"]},
    {"include_defects":False,"bindings":[manual("air")]},
    {"include_defects":False,"coverage_scope":"selected_materials","required_material_ids":["air"]},
    {"bindings":[manual(),manual()]},
    {"bindings":[manual("epoxy")]},
    {"bindings":[manual("water")]},
])
def test_scope_and_inventory_contract_rejections(changes):
    with pytest.raises(ValueError):build(**changes)


@pytest.mark.parametrize("field,value",[("name"," "),("note","\t"),("note",""),("origin",{"kind":"inferred"})])
def test_binding_evidence_cannot_be_blank_or_inferred(field,value):
    b=manual();b[field]=value
    with pytest.raises(ValueError):build(bindings=[b])


@pytest.mark.parametrize("field,value",[
    ("density_kg_m3",.999),("density_kg_m3",30000.1),
    ("relaxed_modulus_gpa",0.),("relaxed_modulus_gpa",1000.1),
    ("unrelaxed_modulus_gpa",0.),("unrelaxed_modulus_gpa",1000.1),
    ("unrelaxed_modulus_gpa",2.),("relaxation_time_us",0.),("relaxation_time_us",100.1),
    ("density_kg_m3",True),("relaxation_time_us",".003"),
    ("density_kg_m3",float("inf")),("relaxed_modulus_gpa",float("nan")),
])
def test_material_ranges_units_and_finite_inputs(field,value):
    with pytest.raises(ValueError):build(bindings=[manual(**{field:value})])


@pytest.mark.parametrize("values",[
    {"density_kg_m3":1.,"relaxed_modulus_gpa":1e-6,"unrelaxed_modulus_gpa":1e-6,"relaxation_time_us":1e-6},
    {"density_kg_m3":30000.,"relaxed_modulus_gpa":1000.,"unrelaxed_modulus_gpa":1000.,"relaxation_time_us":100.},
])
def test_inclusive_material_range_endpoints(values):
    assert build(bindings=[manual(**values)])["bindings"][0]["parameters"] == values


@pytest.mark.parametrize("edit",[
    lambda o:o.update(layer_index=True),lambda o:o.update(layer_index=-1),lambda o:o.update(layer_index=8),
    lambda o:o.update(report_id="not-an-id"),lambda o:o.update(report_id="00000000000000000000000000000000"),
    lambda o:o.update(density_kg_m3=1000.),
])
def test_report_origin_cannot_override_coefficients_or_index_contract(source,edit):
    b=reference(source);edit(b["origin"])
    with pytest.raises(ValueError):core.build_assignment(request(bindings=[b]),{source["id"]:source})


@pytest.mark.parametrize("xy",[(True,1.),("2",1.),(float("nan"),1.),(float("inf"),1.),(-1.,1.),(4.0001,1.),(1.,3.0001)])
def test_global_point_is_strict_finite_and_inside_frozen_specimen(xy):
    with pytest.raises(ValueError):core.inspect_column(build(),*xy)


def test_actual_decimal_coordinate_bits_are_retained_without_snapping():
    x=float(np.nextafter(2.125,np.inf));y=float(np.nextafter(1.5,np.inf))
    c=core.inspect_column(build(),x,y)
    assert c["x_mm"].hex()==x.hex() and c["y_mm"].hex()==y.hex()


@pytest.mark.parametrize("tamper",[
    lambda d:d["snapshots"][d["twin_sha256"]]["objects"][0].update(material="epoxy"),
    lambda d:d["bindings"][0]["parameters"].update(density_kg_m3=999.),
    lambda d:d["bindings"][0].update(assignment_sha256="f"*64),
    lambda d:d["request"].update(include_defects=False),
    lambda d:d["coverage"].update(missing_material_ids=[]),
    lambda d:d["geometry"].update(primitive_count=1),
    lambda d:d["identity"].update(path_contract="unknown-future-path"),
    lambda d:d["ambient_policy"].update(impedance_mrayl=2.),
])
def test_new_inspection_rejects_stale_snapshot_binding_and_contract(tamper):
    d=build(bindings=[manual()]);tamper(d)
    with pytest.raises(ValueError):core.inspect_column(d,2.,1.5)


def test_geometry_budget_rejects_before_path_allocation(monkeypatch):
    d=build()
    monkeypatch.setattr(core,"MAX_WORKSPACE_BYTES",1)
    monkeypatch.setattr(core,"build_column_paths",lambda *a,**k:pytest.fail("Allocated after failed preflight"))
    with pytest.raises(ValueError,match="workspace"):core.inspect_column(d,2.,1.5)


def test_report_and_source_limits_reject_early(monkeypatch,source):
    monkeypatch.setattr(core,"MAX_REPORT_BYTES",100)
    with pytest.raises(ValueError):build()
    monkeypatch.setattr(core,"MAX_REPORT_BYTES",32*1024**2)
    monkeypatch.setattr(core,"MAX_SOURCE_BYTES",100)
    with pytest.raises(ValueError):core.build_assignment(request(bindings=[reference(source)]),{source["id"]:source})


@pytest.fixture(scope="module")
def hbm_document():
    from tools.build_h100_example import h100
    from virtual_microscopy.hbm import compose_hbm
    return build(twin=compose_hbm(h100(),"hbm-6",{"microstructure":{}}))


@pytest.mark.parametrize("xy,count",[
    ([49.42734375,39.876953125],26),([49.52578125,39.876953125],28),
    ([49.469531249999996,39.939453125],26),([49.52578125,39.939453125],28),
    ([49.47421875,39.947265625],26),([49.52578125,39.947265625],28),
])
def test_hbm_six_classes_retain_every_full_depth_layer_beyond_eight(hbm_document,xy,count):
    d=hbm_document;c=core.inspect_column(d,*xy)
    assert d["geometry"]["hbm_assembly_count"]==6 and d["geometry"]["primitive_count"]==574
    assert c["segment_count"]==count and c["segments"][-1]["z_end_mm"]==2.65
    assert c["segments"][0]["material_id"]=="ambient_water" and c["segments"][0]["thickness_mm"]==.2
    assert c["segments"][-1]["material_id"]=="ambient_water"
    assert not c["propagation_available"] and c["coverage"]["missing_material_ids"]
    assert "air" not in c["coverage"]["required_material_ids"]
    assert c["resources"]["estimated_report_bytes"] > core._measure(d)["encoded_bytes"]


def test_full_600_primitive_twin_accepted_and_601_rejected():
    value=twin();value["objects"]=[primitive(str(i),"silicon") for i in range(600)]
    d=build(twin=value);assert core.inspect_column(d,2.,1.5)["segment_count"]==3
    value["objects"].append(primitive("extra","silicon"))
    with pytest.raises(ValueError):build(twin=value)


def test_estimate_and_column_json_reservations_cover_store_headers_and_parent_closure():
    d=build(bindings=[manual()]);c=core.inspect_column(d,2.,1.5)
    publication={**c,"assignment_record":{k:v for k,v in d.items() if k not in("snapshots","snapshot_kinds")},
                 "snapshots":d["snapshots"],"snapshot_kinds":d["snapshot_kinds"]}
    m=core._measure(publication)
    assert m["encoded_bytes"]<c["resources"]["estimated_report_bytes"]
    assert m["expanded_bytes"]<c["resources"]["estimated_report_expanded_bytes"]
    assert c["resources"]["propagation_work_units"]==0


def test_column_forecast_retains_embedded_historical_source_validation_reserve(source):
    d=core.build_assignment(request(bindings=[reference(source)]),{source["id"]:source})
    c=core.inspect_column(d,2.,1.5)
    assert c["resources"]["source_report_count"]==0  # No source file needs to be loaded anew.
    assert c["resources"]["estimated_peak_bytes"]>=core._measure(d)["expanded_bytes"]+256*1024**2
