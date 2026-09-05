"""Declared velocity and surface-reference parameters for SAM depth mapping."""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .schemas import StrictModel


class VelocityLayer(StrictModel):
    end_depth_mm: float = Field(gt=0, le=6)
    sound_speed_m_s: float = Field(gt=0)


class DepthMappingSettings(StrictModel):
    nz: int = Field(default=128, ge=16, le=1024, strict=True)
    z_min_mm: float = Field(default=0, ge=0, le=6)
    z_max_mm: float = Field(gt=0, le=6)
    surface_reference: Literal["source_water_delay", "explicit"] = "source_water_delay"
    surface_time_us: float | None = Field(default=None, ge=0)
    velocity_model: Literal["homogeneous", "layered"] = "homogeneous"
    sound_speed_m_s: float = Field(default=5000, gt=0)
    layers: list[VelocityLayer] = Field(default_factory=list, max_length=128)
    model_evidence: Literal["user_assumed", "user_calibrated", "synthetic_truth"] = "user_assumed"
    model_note: str = Field(default="User-supplied velocity assumptions for time-to-depth mapping; not an acoustic inversion or workbench-verified calibration.", min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_model(self):
        if self.z_max_mm <= self.z_min_mm:
            raise ValueError("Depth maximum must exceed depth minimum.")
        if self.surface_reference == "explicit" and self.surface_time_us is None:
            raise ValueError("An explicit surface reference requires surface_time_us on the saved time axis.")
        if self.surface_reference == "source_water_delay" and self.surface_time_us is not None:
            raise ValueError("Omit surface_time_us when using the source water-delay reference.")
        if self.velocity_model == "homogeneous" and self.layers:
            raise ValueError("Homogeneous mapping requires an empty layers list.")
        if self.velocity_model == "layered":
            if not self.layers:
                raise ValueError("Layered mapping requires at least one layer.")
            endpoints = [layer.end_depth_mm for layer in self.layers]
            if any(b <= a for a, b in zip(endpoints, endpoints[1:])):
                raise ValueError("Layer end depths must strictly increase from an implicit first-layer start at zero.")
        return self


class SamDepthRequest(StrictModel):
    kind: Literal["sam_depth_volume"] = "sam_depth_volume"
    source_dataset_id: str
    mapping: DepthMappingSettings

    @field_validator("source_dataset_id")
    @classmethod
    def canonical_id(cls, value):
        try:
            if str(UUID(value)) != value:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Source dataset ID must be a canonical UUID.") from exc
        return value
