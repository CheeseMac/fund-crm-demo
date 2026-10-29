from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, Field, field_validator

class TenantRegistration(BaseModel):
    tenant_slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,39}$")
    tenant_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=200)
    password: str = Field(min_length=12, max_length=128)

class LoginIn(BaseModel):
    tenant_slug: str
    email: str
    password: str

class UserIn(BaseModel):
    email: str = Field(min_length=5, max_length=200)
    password: str = Field(min_length=12, max_length=128)
    role: Literal["admin", "sales"] = "sales"

class CustomerIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    customer_type: Literal["individual", "institution"]
    tags: list[str] = Field(default_factory=list, max_length=30)
    background_md: str = Field(default="", max_length=30000)

class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    title: str = Field(default="", max_length=100)
    notes_md: str = Field(default="", max_length=10000)

class InterpretIn(BaseModel):
    message: str = Field(min_length=2, max_length=4000)

class ActionDraft(BaseModel):
    """Internal allowed action schema; language models never generate SQL or arbitrary tools."""
    kind: Literal["log_visit", "create_followup", "record_need", "record_holding"]
    customer_name: str = Field(min_length=1, max_length=120)
    contact_name: str | None = Field(default=None, max_length=100)
    occurred_on: date | None = None
    due_on: date | None = None
    cadence_days: int | None = Field(default=None, ge=1, le=3650)
    summary_md: str | None = Field(default=None, max_length=5000)
    description: str | None = Field(default=None, max_length=500)
    category: str = Field(default="other", max_length=40)
    product_name: str | None = Field(default=None, max_length=160)
    fund_code: str | None = Field(default=None, max_length=32)
    amount_cny: Decimal | None = Field(default=None, ge=0)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("customer_name", "contact_name", "product_name", mode="before")
    @classmethod
    def trim(cls, v):
        return v.strip() if isinstance(v, str) else v

class ResolvedAction(ActionDraft):
    customer_id: str
    contact_id: str | None = None

class MarketSignalIn(BaseModel):
    external_key: str = Field(min_length=4, max_length=128)
    kind: Literal["risk", "opportunity", "style_rotation", "sentiment_low", "sentiment_high"]
    severity: int = Field(ge=1, le=5)
    tags: list[str] = Field(min_length=1, max_length=20)
    headline: str = Field(min_length=4, max_length=240)
    details_md: str = Field(default="", max_length=15000)
    evidence_url: str | None = Field(default=None, max_length=500)
    happened_on: date

class TaskIn(BaseModel):
    customer_id: str
    contact_id: str | None = None
    due_on: date
    cadence_days: int | None = Field(default=None, ge=1, le=3650)
    reason_md: str = Field(default="", max_length=5000)

class HoldingIn(BaseModel):
    product_name: str = Field(min_length=2, max_length=160)
    fund_code: str | None = Field(default=None, max_length=32)
    amount_cny: Decimal | None = Field(default=None, ge=0)
    as_of: date
    tags: list[str] = Field(default_factory=list, max_length=20)
    note_md: str = Field(default="", max_length=5000)

class PrefsIn(BaseModel):
    lead_days: int | None = Field(default=None, ge=0, le=30)
    timezone: str | None = Field(default=None, max_length=64)
    channels: list[str] | None = Field(default=None, max_length=10)

class MediaInitiateIn(BaseModel):
    customer_id: str
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=120)
    declared_size: int = Field(gt=0)

class MeetingIn(BaseModel):
    customer_id: str
    title: str = Field(min_length=2, max_length=200)
    occurred_on: date
    contact_id: str | None = None
    asset_ids: list[str] = Field(default_factory=list, max_length=50)
