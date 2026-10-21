"""Shared strict JSON contract, independent of stage objects."""
from pydantic import BaseModel,ConfigDict


class Contract(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,str_strip_whitespace=True)
