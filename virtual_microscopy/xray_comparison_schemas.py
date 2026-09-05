"""Explicit normalization and observation policies for saved X-ray comparisons."""
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .datasets import checked_id
from .schemas import StrictModel


class XrayComparisonRequest(StrictModel):
    reference_dataset_id: str
    candidate_dataset_id: str
    product: Literal["counts", "transmission", "line_integrals"] = "transmission"
    normalization: Literal["native", "per_source_incident"] = "native"
    observation_policy: Literal["same_kind", "observed_vs_expected"] = "same_kind"
    view_index: int | None = Field(default=None, ge=0, le=719, strict=True)
    detector_row: int | None = Field(default=None, ge=0, le=255, strict=True)
    detector_col: int | None = Field(default=None, ge=0, le=255, strict=True)

    @field_validator("reference_dataset_id", "candidate_dataset_id")
    @classmethod
    def canonical_identifier(cls, value):
        return checked_id(value)

    @model_validator(mode="after")
    def count_policy(self):
        if self.product == "counts" and (self.normalization != "native" or self.observation_policy != "same_kind"):
            raise ValueError("Raw counts require native normalization and the same observed/expected kind; select transmission or line_integrals for explicit normalized comparisons.")
        return self
