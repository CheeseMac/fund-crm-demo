from typing import Literal
from pydantic import BaseModel, Field

class CustomerPatch(BaseModel):
    tags: list[str] | None = Field(default=None, max_length=30)
    background_md: str | None = Field(default=None, max_length=30000)

class NeedPatch(BaseModel):
    status: Literal["open", "fulfilled", "closed"] | None = None
    description: str | None = Field(default=None, min_length=2, max_length=500)
    tags: list[str] | None = Field(default=None, max_length=20)
    details_md: str | None = Field(default=None, max_length=10000)
