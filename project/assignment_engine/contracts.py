from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

Method = Literal['capacity_aware','fuzzy_optimal','ai_assisted']
FACTORS=('zone','segment','seniority','sector_experience','response_speed','complexity','capacity')


class Configuration(BaseModel):
    model_config=ConfigDict(extra='forbid')
    balance_weight: float=Field(default=0.60,ge=0,le=1)
    amount_weight: float=Field(default=0.5,ge=0,le=1)
    weights: dict[str,float] | None=None

    @field_validator('weights')
    @classmethod
    def valid_weights(cls,value):
        if value is not None and (set(value)!=set(FACTORS) or any(not 0<=v<=1 for v in value.values()) or sum(value.values())<=0):
            raise ValueError('Provide every factor with nonnegative weights, a positive sum, and values <= 1')
        return value


class StateRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    record_ids: list[str]=Field(min_length=1,max_length=100)

    @field_validator('record_ids',mode='before')
    @classmethod
    def normalize_ids(cls,value):
        if not isinstance(value,list) or any(isinstance(x,bool) or not isinstance(x,(str,int)) or not str(x).strip() for x in value):
            raise ValueError('Record IDs must be nonempty strings or integers')
        result=[str(x) for x in value]
        if len(set(result))!=len(result):raise ValueError('Duplicate record IDs are not allowed')
        return result


class PreviewRequest(StateRequest):
    method: Method
    configuration: Configuration=Field(default_factory=Configuration)


class ExecuteRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    approved: Literal[True]
