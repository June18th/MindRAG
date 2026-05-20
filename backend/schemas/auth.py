"""Auth/user request and response schemas."""
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


# --- Request schemas ---

class UserRegisterRequest(BaseModel):
    username: str
    password: str
    inviteCode: str | None = None


class UserLoginRequest(BaseModel):
    username: str
    password: str


class RefreshTokenRequest(BaseModel):
    refreshToken: str


class PrimaryOrgRequest(BaseModel):
    primaryOrg: str


# --- Response schemas ---

class LoginData(BaseModel):
    token: str
    refreshToken: str


class UserProfileData(BaseModel):
    id: int
    username: str
    role: str
    orgTags: list[str] = []
    primaryOrg: str | None = None
    createdAt: datetime | None = None
    updatedAt: datetime | None = None


class OrgTagDetail(BaseModel):
    tagId: str
    name: str
    description: str | None = None
    uploadMaxSizeBytes: int | None = None
    uploadMaxSizeMb: int | None = None


class OrgTagsData(BaseModel):
    orgTags: list[str]
    primaryOrg: str | None = None
    orgTagDetails: list[OrgTagDetail] = []


class UploadOrgsData(BaseModel):
    orgTags: list[str]
    primaryOrg: str | None = None


class QuotaView(BaseModel):
    used: int = 0
    limit: int = 0


class UsageSnapshotData(BaseModel):
    day: str
    chatCount: int = 0
    llm: QuotaView = Field(default_factory=QuotaView)
    embedding: QuotaView = Field(default_factory=QuotaView)


class TokenRecordItem(BaseModel):
    id: int
    recordDate: date | None = None
    tokenType: str  # LLM or EMBEDDING
    changeType: str  # INCREASE or CONSUME
    amount: int = 0
    balanceBefore: int | None = None
    balanceAfter: int | None = None
    reason: str | None = None
    remark: str | None = None
    createdAt: datetime | None = None
    requestCount: int = 0


class TokenRecordsPage(BaseModel):
    content: list[TokenRecordItem]
    totalElements: int
    totalPages: int
    number: int
    size: int
    first: bool
    last: bool
    empty: bool
