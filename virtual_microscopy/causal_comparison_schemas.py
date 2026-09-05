"""Explicit same-excitation comparisons of immutable causal recordings."""
from typing import Literal

from pydantic import Field, StrictFloat, field_validator, model_validator

from .datasets import checked_id
from .schemas import StrictModel


class CausalComparisonRequest(StrictModel):
    name: str = Field(default="", max_length=160)
    reference_dataset_id: str
    candidate_dataset_id: str
    policy: Literal["same_excitation_v1"] = "same_excitation_v1"
    gate_start_us: StrictFloat = Field(ge=0, le=12)
    gate_end_us: StrictFloat = Field(ge=0, le=12)
    x_index: int | None = Field(default=None, ge=0, le=63, strict=True)
    y_index: int | None = Field(default=None, ge=0, le=63, strict=True)
    time_index: int | None = Field(default=None, ge=0, le=2048, strict=True)

    @field_validator("reference_dataset_id", "candidate_dataset_id")
    @classmethod
    def identifier(cls, value):
        return checked_id(value)

    @model_validator(mode="after")
    def ordered_gate(self):
        if self.gate_end_us <= self.gate_start_us:
            raise ValueError("A causal comparison requires finite gate start < end.")
        return self
