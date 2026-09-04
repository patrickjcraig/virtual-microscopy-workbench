"""Independent parallel-beam acquisition settings for saved X-ray views."""

from typing import Literal

from pydantic import Field, model_validator

from .schemas import Coordinate, StrictModel, Twin


class XrayVolumeSettings(StrictModel):
    geometry_nx: int = Field(default=96, ge=16, le=256, strict=True)
    geometry_ny: int = Field(default=96, ge=16, le=256, strict=True)
    geometry_nz: int = Field(default=256, ge=32, le=1024, strict=True)
    detector_cols: int = Field(default=96, ge=16, le=256, strict=True)
    detector_rows: int = Field(default=64, ge=16, le=256, strict=True)
    detector_width_mm: float | None = Field(default=None, ge=0.05, le=300)
    detector_height_mm: float | None = Field(default=None, ge=0.05, le=300)
    detector_offset_u_mm: float = Field(default=0, ge=-100, le=100)
    detector_offset_v_mm: float = Field(default=0, ge=-100, le=100)
    rotation_center_mm: tuple[Coordinate, Coordinate, Coordinate] | None = None
    views: int = Field(default=72, ge=1, le=720, strict=True)
    angle_start_deg: float = Field(default=0, ge=-360, le=360)
    angle_span_deg: float = Field(default=360, gt=0, le=360)
    energy_kev: float = Field(default=80, ge=40, le=150)
    photons: int = Field(default=50000, ge=1000, le=1000000, strict=True)
    noise: bool = True
    seed: int = Field(default=42, ge=0, le=4294967295, strict=True)
    detector_fwhm_mm: float = Field(default=0.02, ge=0, le=1)
    include_defects: bool = True


class XrayVolumeRequest(StrictModel):
    kind: Literal["xray_projection_volume"] = "xray_projection_volume"
    twin: Twin
    acquisition: XrayVolumeSettings = Field(default_factory=XrayVolumeSettings)

    @model_validator(mode="after")
    def validate_center(self):
        center = self.acquisition.rotation_center_mm
        if center is not None and any(c > s for c, s in zip(center, self.twin.size_mm)):
            raise ValueError("Rotation center must lie inside the specimen bounds.")
        return self
