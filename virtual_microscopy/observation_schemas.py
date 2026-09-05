"""Strict derived finite coherent observation requests."""
from typing import Literal

from pydantic import Field, StrictFloat, field_validator

from .datasets import checked_id
from .schemas import StrictModel


class ObservationRequest(StrictModel):
    kind: Literal["sam_coherent_observation_volume"] = "sam_coherent_observation_volume"
    name: str = Field(default="", max_length=160)
    source_dataset_id: str
    operator: Literal["binomial_3x3_coherent_v1"] = "binomial_3x3_coherent_v1"
    absolute_tolerance: StrictFloat = Field(default=1e-7, ge=1e-12, le=1e-3)

    @field_validator("source_dataset_id")
    @classmethod
    def identifier(cls, value):
        return checked_id(value)
