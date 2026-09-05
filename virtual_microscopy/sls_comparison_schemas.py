"""Strict saved-report SLS comparisons; no source reconstruction or inference."""
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictFloat, field_validator, model_validator

from .schemas import StrictModel


class SLSComparisonRequest(StrictModel):
    reference_report_id: str
    candidate_report_id: str
    mode: Literal["spectrum_only", "spectrum_and_reflected_rf"] = "spectrum_and_reflected_rf"
    gate_start_us: StrictFloat | None = Field(default=None, ge=0, le=12)
    gate_end_us: StrictFloat | None = Field(default=None, ge=0, le=12)
    frequency_index: int | None = Field(default=None, ge=0, le=8192, strict=True)
    time_index: int | None = Field(default=None, ge=0, le=2048, strict=True)

    @field_validator("reference_report_id", "candidate_report_id")
    @classmethod
    def canonical_id(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("SLS comparison source IDs must be canonical UUID strings.")
        return value

    @model_validator(mode="after")
    def recording_scope(self):
        if (self.gate_start_us is None) != (self.gate_end_us is None):
            raise ValueError("Supply both inclusive gate endpoints or neither.")
        if self.gate_start_us is not None and self.gate_end_us < self.gate_start_us:
            raise ValueError("Gate end cannot precede gate start.")
        if self.mode == "spectrum_only" and (self.gate_start_us is not None or self.time_index is not None):
            raise ValueError("Spectrum-only comparisons have no RF gate or time cursor.")
        return self
