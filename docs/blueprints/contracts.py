"""Reference contracts only, NOT connected to the existing application.

Integrate these gradually behind app/application and app/infrastructure.
No network calls, DB writes, third-party LLM, or market data included.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class ActorContext(BaseModel):
    """Created by trusted auth middleware; NEVER accept actor from an MCP tool parameter."""
    tenant_id: str
    user_id: str
    scopes: frozenset[str] = frozenset()
    actor_type: Literal["user", "service"] = "user"


class DomainEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str = Field(min_length=3, max_length=120)
    schema_version: int = Field(default=1, ge=1)
    tenant_id: str
    actor_id: str | None = None
    source: str = Field(min_length=2, max_length=100)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    subject: dict[str, str]
    idempotency_key: str = Field(min_length=8, max_length=400)

    @model_validator(mode="after")
    def require_aware_time(self):
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must have timezone")
        return self


class DraftAction(BaseModel):
    kind: Literal["log_visit", "record_need", "create_followup", "record_holding"]
    customer_id: str
    contact_id: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    # Never assert that a meeting transcript equals verified investment holdings.
    evidence_ref_ids: list[str] = Field(default_factory=list)
    source_confidence: float | None = Field(default=None, ge=0, le=1)


class InterpretRequest(BaseModel):
    text: str = Field(min_length=2, max_length=20000)
    customer_context: list[dict[str, str]] = Field(default_factory=list)
    request_id: str = Field(default_factory=lambda: str(uuid4()))


class DraftActions(BaseModel):
    actions: list[DraftAction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    model_id: str | None = None
    prompt_version: str | None = None


class TranscriptSegment(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    speaker_ref: str | None = None
    text: str
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def check_range(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class TranscriptionRequest(BaseModel):
    asset_id: str
    language: str = "zh"
    require_speaker_diarization: bool = False


class TranscriptDraft(BaseModel):
    asset_id: str
    segments: list[TranscriptSegment]
    model_id: str
    status: Literal["draft", "needs_review"] = "needs_review"


class SummaryRequest(BaseModel):
    report_type: Literal["daily", "weekly", "meeting"]
    period_start: datetime
    period_end: datetime
    metrics: dict[str, Any] = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)


class SummaryDraft(BaseModel):
    body_md: str
    source_ids: list[str]
    model_id: str | None = None
    warnings: list[str] = Field(default_factory=list)


class UploadRequest(BaseModel):
    customer_id: str
    filename: str
    content_type: str
    declared_size: int = Field(gt=0, le=2_000_000_000)
    sha256: str | None = None


class UploadTicket(BaseModel):
    asset_id: str
    upload_url: str
    expires_at: datetime
    allowed_method: Literal["PUT", "POST"]


class MarketObservation(BaseModel):
    source_id: str
    external_key: str
    observed_at: datetime
    symbol_or_topic: str
    kind: str
    payload: dict[str, Any]
    quality: Literal["raw", "validated", "rejected"] = "raw"


# Ports: backend integrations. Use dependency injection or an explicit factory.
class IntentExtractor(Protocol):
    async def extract(self, request: InterpretRequest) -> DraftActions: ...


class Transcriber(Protocol):
    async def transcribe(self, request: TranscriptionRequest) -> TranscriptDraft: ...


class Summarizer(Protocol):
    async def summarize(self, request: SummaryRequest) -> SummaryDraft: ...


class MediaStore(Protocol):
    async def initiate_upload(self, actor: ActorContext, request: UploadRequest) -> UploadTicket: ...


class MarketDataProvider(Protocol):
    async def observations(self, *, start: datetime, end: datetime) -> list[MarketObservation]: ...


class CRMApplication(Protocol):
    """One shared service used by REST, MCP and internal trusted workers."""
    def list_due_followups(self, actor: ActorContext, *, before: datetime) -> list[dict]: ...

    def prepare_actions(self, actor: ActorContext, actions: DraftActions) -> str:
        """Creates a PendingAction only, never writes CRM facts."""
        ...

    def confirm_actions(
        self,
        actor: ActorContext,
        pending_action_id: str,
        one_time_approval_token: str,
    ) -> list[str]:
        """Validates approval, scope, customer ownership, and a DB transaction."""
        ...
