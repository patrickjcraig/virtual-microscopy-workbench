"""Strict requests for read-only comparisons of completed SAM recordings."""

from pydantic import Field, field_validator, model_validator

from .datasets import checked_id
from .schemas import StrictModel


class SamComparisonRequest(StrictModel):
    reference_dataset_id: str
    candidate_dataset_id: str
    gate_start_us: float = Field(ge=0, le=12)
    gate_end_us: float = Field(gt=0, le=12)
    x_index: int | None = Field(default=None, ge=0, le=255, strict=True)
    y_index: int | None = Field(default=None, ge=0, le=255, strict=True)

    @field_validator("reference_dataset_id", "candidate_dataset_id")
    @classmethod
    def canonical_identifier(cls, value):
        return checked_id(value)

    @model_validator(mode="after")
    def gate_order(self):
        if self.gate_end_us <= self.gate_start_us:
            raise ValueError("Comparison gate end must exceed its start.")
        return self
