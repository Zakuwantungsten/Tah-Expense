from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class RefreshRequest(StrictModel):
    refresh_token: str


class ChangePasswordRequest(StrictModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=1024)


class UserCreate(StrictModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=10, max_length=1024)
    role: Literal["admin", "cashier", "accountant"]
    full_name: str = Field(min_length=1, max_length=200)
    active: bool = True


class UserUpdate(StrictModel):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    role: Literal["admin", "cashier", "accountant"] | None = None
    active: bool | None = None
    password: str | None = Field(None, min_length=10, max_length=1024)


class CategoryWrite(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    color: str = "#4A90D9"
    icon: str = "mdi.tag-outline"
    sidebar_name: str = ""
    show_in_sidebar: bool = False
    sort_order: int = 0
    requires_receipt: bool = False
    requires_truck: bool = True
    lock_description: bool = False
    active: bool = True
    account_type: str = ""
    ref_num: str = ""
    account_number: str = ""
    currency: str = ""
    coa_description: str = ""


class SubtableWrite(StrictModel):
    parent_key: str
    parent_category: str
    name: str
    match: str = ""
    active: bool = True
    sort_order: int = 0


class RuleWrite(StrictModel):
    pattern: str
    category_id: str
    category_name: str
    priority: int = Field(5, ge=1, le=10)
    match_type: Literal["contains", "exact", "startswith", "fuzzy"] = "contains"
    active: bool = True


class MappingWrite(StrictModel):
    description: str
    category_id: str
    category_name: str


class FleetWrite(StrictModel):
    number: str = Field(min_length=1, max_length=40)
    active: bool = True

    @field_validator("number")
    @classmethod
    def normalize_number(cls, value: str) -> str:
        return " ".join(value.upper().split())


class PeopleWrite(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    active: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(value.upper().split())


class PatchDocument(StrictModel):
    values: dict[str, Any]

    @field_validator("values")
    @classmethod
    def prevent_unsafe_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        blocked = {"_id", "password_hash", "created_at"}
        if blocked.intersection(value):
            raise ValueError("immutable or sensitive field included")
        return value


class BackupRestoreRequest(StrictModel):
    """Admin confirmation must re-type the exact backup filename."""

    filename: str = Field(min_length=1, max_length=260)
    confirm_filename: str = Field(min_length=1, max_length=260)

    @field_validator("filename", "confirm_filename")
    @classmethod
    def reject_path_components(cls, value: str) -> str:
        if any(part in value for part in ("/", "\\", "..")):
            raise ValueError("filename must not contain path components")
        return value
