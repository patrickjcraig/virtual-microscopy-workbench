"""Independent acquisition schema for persisted pulse-echo SAM volumes."""

from typing import Literal

from pydantic import Field, model_validator

from .schemas import Coordinate, StrictModel, Twin


class SamVolumeSettings(StrictModel):
    scan_nx: int = Field(default=64, ge=16, le=256, strict=True)
    scan_ny: int = Field(default=64, ge=16, le=256, strict=True)
    depth_samples: Literal[128, 256, 512, 1024] = 512
    roi_mm: tuple[Coordinate, Coordinate, Coordinate, Coordinate] | None = None
    frequency_mhz: float = Field(default=50, ge=10, le=150)
    fractional_bandwidth: float = Field(default=0.5, ge=0.2, le=1)
    focus_mm: float = Field(default=0.85, ge=0, le=6)
    record_start_us: float = Field(default=0, ge=0, le=12)
    record_duration_us: float = Field(default=2, ge=0.05, le=12)
    sample_rate_mhz: float = Field(default=400, gt=0, le=2400)
    water_standoff_mm: float = Field(default=0, ge=0, le=5)
    include_defects: bool = True

    @model_validator(mode="after")
    def validate_acquisition(self):
        if self.record_start_us + self.record_duration_us > 12 + 1e-10:
            raise ValueError("The recording window must end at or before 12 us.")
        if self.sample_rate_mhz < 8 * self.frequency_mhz:
            raise ValueError("Sample rate must be at least eight times the center frequency (8 RF samples per period).")
        if self.roi_mm is not None:
            x0, y0, x1, y1 = self.roi_mm
            if x1 - x0 < 0.05 or y1 - y0 < 0.05:
                raise ValueError("ROI width and height must be at least 0.05 mm.")
        return self


class SamVolumeRequest(StrictModel):
    twin: Twin
    acquisition: SamVolumeSettings = Field(default_factory=SamVolumeSettings)

    @model_validator(mode="after")
    def validate_specimen_bounds(self):
        if self.acquisition.focus_mm > self.twin.size_mm[2]:
            raise ValueError("Acoustic focus must lie within the specimen depth.")
        roi = self.acquisition.roi_mm
        if roi is not None and (roi[2] > self.twin.size_mm[0] or roi[3] > self.twin.size_mm[1]):
            raise ValueError("ROI bounds must lie inside the specimen.")
        return self
