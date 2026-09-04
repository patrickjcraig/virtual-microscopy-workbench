"""Strict public input schema. Coordinates and extents are in millimetres."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

Positive = Annotated[float, Field(gt=0, le=100, allow_inf_nan=False)]
Coordinate = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
MaterialId = Literal["silicon", "copper", "solder", "epoxy", "fr4", "air"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Primitive(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    shape: Literal["box", "sphere", "cylinder"]
    material: MaterialId
    center_mm: tuple[Coordinate, Coordinate, Coordinate]
    size_mm: tuple[Positive, Positive, Positive]
    role: Literal["structure", "defect"] = "structure"
    display_label: str | None = Field(default=None, max_length=48)
    assembly_id: str | None = Field(default=None, min_length=1, max_length=64)
    layer_role: Literal["base_die", "dram_die", "interdie_gap", "cap", "underfill", "contact"] | None = None

    @model_validator(mode="after")
    def check_shape(self):
        sx, sy, sz = self.size_mm
        if self.shape in ("sphere", "cylinder") and abs(sx - sy) > 1e-8:
            raise ValueError("Sphere/cylinder x and y diameters must be equal.")
        if self.shape == "sphere" and abs(sx - sz) > 1e-8:
            raise ValueError("All sphere diameters must be equal.")
        return self


class Settings(StrictModel):
    resolution: Literal[64, 128, 192] = 128
    depth_samples: Literal[128, 256, 512, 1024] | None = None
    roi_mm: tuple[Coordinate, Coordinate, Coordinate, Coordinate] | None = None
    energy_kev: float = Field(default=80, ge=40, le=150)
    angle_deg: float = Field(default=0, ge=-45, le=45)
    photons: int = Field(default=50000, ge=1000, le=1000000)
    noise: bool = True
    frequency_mhz: float = Field(default=50, ge=10, le=150)
    gate_start_us: float = Field(default=0.42, ge=0, le=10)
    gate_end_us: float = Field(default=0.56, gt=0, le=12)
    focus_mm: float = Field(default=0.5, ge=0, le=6)
    probe_x_mm: float = Field(default=3.1, ge=0, le=100)
    probe_y_mm: float = Field(default=3.1, ge=0, le=100)
    include_defects: bool = True
    seed: int = Field(default=42, ge=0, le=4294967295)

    @model_validator(mode="after")
    def check_gate(self):
        if self.gate_end_us <= self.gate_start_us:
            raise ValueError("Gate end must be greater than gate start.")
        if self.roi_mm is not None:
            x0, y0, x1, y1 = self.roi_mm
            if x1 - x0 < 0.05 or y1 - y0 < 0.05:
                raise ValueError("ROI width and height must be at least 0.05 mm.")
            if self.angle_deg != 0:
                raise ValueError("ROI X-ray scans currently require 0° incidence; use the full specimen for tilted scans.")
            if not (x0 <= self.probe_x_mm <= x1 and y0 <= self.probe_y_mm <= y1):
                raise ValueError("Probe coordinates must lie inside the selected ROI.")
        return self


class ReferenceSource(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=240)
    url: HttpUrl


class PublishedFact(StrictModel):
    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=500)
    source_ids: list[str] = Field(min_length=1, max_length=8)


class SpecimenReference(StrictModel):
    product: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=2000)
    sources: list[ReferenceSource] = Field(min_length=1, max_length=16)
    published_facts: list[PublishedFact] = Field(min_length=1, max_length=24)
    assumptions: list[Annotated[str, Field(min_length=1, max_length=1600)]] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def check_sources(self):
        ids = [source.id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("Reference source ids must be unique.")
        for fact in self.published_facts:
            if not set(fact.source_ids).issubset(ids):
                raise ValueError(f"Published fact '{fact.label}' cites an unknown source id.")
        return self


class HBMParameters(StrictModel):
    """Assumed layered geometry; z increases downwards from the exposed surface."""

    center_xy_mm: tuple[Coordinate, Coordinate]
    footprint_mm: tuple[Positive, Positive] = (8, 9)
    bottom_z_mm: float = Field(default=0.82, gt=0, le=6)
    die_count: Literal[8, 12] = 8
    die_thickness_um: float = Field(default=50, ge=5, le=200)
    gap_um: float = Field(default=15, ge=1, le=100)
    base_thickness_um: float = Field(default=70, ge=5, le=300)
    cap_thickness_um: float = Field(default=30, ge=1, le=300)
    functional_state: Literal["enabled", "disabled", "unknown"] = "unknown"
    physical_present: bool = True
    evidence: str = Field(default="Assumed layered construction; not specimen-specific measured geometry.", min_length=1, max_length=1000)

    @model_validator(mode="after")
    def check_stack_height(self):
        height = (self.base_thickness_um + self.die_count * (self.die_thickness_um + self.gap_um) + self.cap_thickness_um) / 1000
        if height > self.bottom_z_mm + 1e-8:
            raise ValueError("HBM stack height extends above the specimen surface.")
        return self


class HBMStack(HBMParameters):
    id: str = Field(pattern=r"^hbm-[1-9][0-9]*$", max_length=64)
    name: str = Field(min_length=1, max_length=120)


class HBMParameterUpdate(StrictModel):
    center_xy_mm: tuple[Coordinate, Coordinate] | None = None
    footprint_mm: tuple[Positive, Positive] | None = None
    bottom_z_mm: float | None = Field(default=None, gt=0, le=6)
    die_count: Literal[8, 12] | None = None
    die_thickness_um: float | None = Field(default=None, ge=5, le=200)
    gap_um: float | None = Field(default=None, ge=1, le=100)
    base_thickness_um: float | None = Field(default=None, ge=5, le=300)
    cap_thickness_um: float | None = Field(default=None, ge=1, le=300)
    functional_state: Literal["enabled", "disabled", "unknown"] | None = None
    physical_present: bool | None = None
    evidence: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def check_no_explicit_nulls(self):
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("HBM parameter patches cannot contain null values.")
        return self


class ImageReference(StrictModel):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    width_px: int = Field(gt=0, le=100000)
    height_px: int = Field(gt=0, le=100000)
    pixel_size_um: float = Field(gt=0, le=100000)
    scale_status: Literal["user_estimate", "calibrated"] = "user_estimate"
    title: str = Field(min_length=1, max_length=240)
    source_note: str = Field(min_length=1, max_length=2000)


class Twin(StrictModel):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    size_mm: tuple[Positive, Positive, Positive]
    objects: list[Primitive] = Field(min_length=1, max_length=600)
    reference: SpecimenReference | None = None
    recommended_settings: Settings | None = None
    hbm_assemblies: list[HBMStack] | None = Field(default=None, max_length=12)
    image_reference: ImageReference | None = None

    @model_validator(mode="after")
    def check_geometry(self):
        sx, sy, sz = self.size_mm
        if sx > 100 or sy > 100 or sz > 6:
            raise ValueError("The workbench supports specimens up to 100 × 100 × 6 mm.")
        if min(sx, sy) < 0.05 or sz < 0.01:
            raise ValueError("Specimen must span at least 0.05 mm in x/y and 0.01 mm in z.")
        ids = [obj.id for obj in self.objects]
        if len(ids) != len(set(ids)):
            raise ValueError("Object ids must be unique.")
        for obj in self.objects:
            for axis, (c, s, bound) in enumerate(zip(obj.center_mm, obj.size_mm, self.size_mm)):
                if c - s / 2 < -1e-8 or c + s / 2 > bound + 1e-8:
                    raise ValueError(f"Object '{obj.id}' extends beyond specimen axis {axis}.")
        preset = self.recommended_settings
        if preset is not None and (preset.probe_x_mm > sx or preset.probe_y_mm > sy or preset.focus_mm > sz):
            raise ValueError("Recommended probe and focus must be inside the specimen extent.")
        if preset is not None and preset.roi_mm is not None and (preset.roi_mm[2] > sx or preset.roi_mm[3] > sy):
            raise ValueError("Recommended ROI must be inside the specimen extent.")
        # The import boundary checks that editable assembly metadata and the
        # actual material objects describe the same physical construction.
        from .hbm import validate_hbm_geometry
        validate_hbm_geometry(self)
        return self


class HBMUpdateRequest(StrictModel):
    twin: Twin
    assembly_id: str = Field(min_length=1, max_length=64)
    parameters: HBMParameterUpdate


class SimulationRequest(StrictModel):
    twin: Twin
    settings: Settings = Field(default_factory=Settings)

    @model_validator(mode="after")
    def check_probe(self):
        sx, sy, sz = self.twin.size_mm
        if self.settings.probe_x_mm > sx or self.settings.probe_y_mm > sy:
            raise ValueError("Probe coordinates must be inside the specimen x/y extent.")
        if self.settings.focus_mm > sz:
            raise ValueError("Acoustic focus must lie inside the specimen depth extent.")
        if self.settings.roi_mm is not None and (self.settings.roi_mm[2] > sx or self.settings.roi_mm[3] > sy):
            raise ValueError("ROI bounds must lie inside the specimen.")
        return self
