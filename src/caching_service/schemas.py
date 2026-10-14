from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

Text = Annotated[StrictStr, Field(max_length=10_000)]


class PayloadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    list_1: list[Text] = Field(max_length=1_000)
    list_2: list[Text] = Field(max_length=1_000)

    @model_validator(mode="after")
    def equal_lengths(self) -> "PayloadInput":
        if len(self.list_1) != len(self.list_2):
            raise ValueError("list_1 and list_2 must have the same length")
        return self


class PayloadCreated(BaseModel):
    id: str


class PayloadOutput(BaseModel):
    output: str
