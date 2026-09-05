"""Explicit, unfocused causal column recordings; no legacy SAM defaults."""
from typing import Literal

from pydantic import ConfigDict, Field, StrictBool, StrictFloat, model_validator

from .layered_schemas import CausalGammaPulseSettings
from .schemas import StrictModel, Twin


class CausalSamVolumeSettings(CausalGammaPulseSettings):
    model_config = ConfigDict(validate_default=True)
    path_model: Literal["continuous_columns_v1"] = "continuous_columns_v1"
    observation_model: Literal["independent_columns_v1"] = "independent_columns_v1"
    scan_nx: int = Field(default=32, ge=16, le=64, strict=True)
    scan_ny: int = Field(default=64, ge=16, le=64, strict=True)
    roi_mm: tuple[StrictFloat, StrictFloat, StrictFloat, StrictFloat] | None = None
    include_defects: StrictBool = True

    @model_validator(mode="after")
    def valid_roi(self):
        if self.roi_mm is not None:
            x0, y0, x1, y1 = self.roi_mm
            if min(x0, y0) < 0 or max(x1, y1) > 100 or x1-x0 < .05 or y1-y0 < .05:
                raise ValueError("Causal ROI must be ordered, inside 0–100 mm, and at least 0.05 mm wide and high.")
        return self


class CausalSamVolumeRequest(StrictModel):
    kind: Literal["sam_causal_rf_volume"]
    twin: Twin
    acquisition: CausalSamVolumeSettings = Field(default_factory=CausalSamVolumeSettings)

    @model_validator(mode="after")
    def inside_specimen(self):
        roi = self.acquisition.roi_mm
        if roi is not None and (roi[2] > self.twin.size_mm[0] or roi[3] > self.twin.size_mm[1]):
            raise ValueError("Causal ROI bounds must lie inside the specimen.")
        return self
