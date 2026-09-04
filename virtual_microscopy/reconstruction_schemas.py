"""Bounded CPU parallel-beam filtered-backprojection inputs."""

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .schemas import Coordinate, StrictModel


class ReconstructionSettings(StrictModel):
    nx: int = Field(default=96, ge=16, le=256, strict=True)
    ny: int = Field(default=64, ge=16, le=256, strict=True)
    nz: int = Field(default=64, ge=16, le=256, strict=True)
    bounds_mm: tuple[Coordinate, Coordinate, Coordinate, Coordinate, Coordinate, Coordinate] | None = None
    filter: Literal["ram_lak", "hann"] = "hann"
    frequency_cutoff: float = Field(default=1, ge=0.1, le=1)
    invalid_policy: Literal["reject", "interpolate"] = "interpolate"
    truncation_policy: Literal["reject", "allow"] = "reject"

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.bounds_mm is not None and any(self.bounds_mm[i + 1] <= self.bounds_mm[i] for i in (0, 2, 4)):
            raise ValueError("Reconstruction bounds must be ordered [xmin,xmax,ymin,ymax,zmin,zmax].")
        return self


class ReconstructionRequest(StrictModel):
    kind: Literal["xray_reconstruction"] = "xray_reconstruction"
    source_dataset_id: str
    reconstruction: ReconstructionSettings = Field(default_factory=ReconstructionSettings)

    @field_validator("source_dataset_id")
    @classmethod
    def canonical_id(cls, value):
        try:
            if str(UUID(value)) != value:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Source dataset ID must be a canonical UUID.") from exc
        return value
