from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class RefreshRequest(StrictModel):
    refresh_token: str


class ChangePasswordRequest(StrictModel):
    current_password: str
    # Complexity is enforced against live security_* settings in the route.
    new_password: str = Field(min_length=1, max_length=1024)


class UserCreate(StrictModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)
    role: Literal["admin", "cashier", "accountant"]
    full_name: str = Field(min_length=1, max_length=200)
    active: bool = True


class UserUpdate(StrictModel):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    role: Literal["admin", "cashier", "accountant"] | None = None
    active: bool | None = None
    password: str | None = Field(None, min_length=1, max_length=1024)


class UserBanRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=500)


class UserResetPasswordRequest(StrictModel):
    password: str = Field(min_length=1, max_length=1024)


class CategoryWrite(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    color: str = "#4A90D9"
    icon: str = "mdi.tag-outline"
    sidebar_name: str = ""
    show_in_sidebar: bool = False
    show_in_cashier_sidebar: bool = False
    sort_order: int = 0
    requires_receipt: bool = False
    requires_truck: bool = True
    lock_description: bool = False
    restrict_in_pdf: bool = False
    restrict_in_excel: bool = False
    is_supplier: bool = False
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
    payee: str = ""


class MappingBulkUpsert(StrictModel):
    mappings: list[MappingWrite] = Field(min_length=1, max_length=500)


class MappingBulkDelete(StrictModel):
    ids: list[str] = Field(min_length=1, max_length=10000)


class ApplyDescriptionMappingRequest(StrictModel):
    """Stamp Payee + Item onto matching open register rows in one update_many.

    When ``clear`` is True, matching open rows have Payee / Item / category
    wiped (Day Issues Undo). Item fields are then optional.
    """

    description_keys: list[str] = Field(min_length=1, max_length=500)
    payee: str = ""
    category_name: str = Field("", max_length=200)
    category_id: str | None = None
    clear: bool = False
    dates: list[str] = Field(default_factory=list, max_length=366)
    cashier_id: str | None = None
    client_job_id: str | None = Field(None, max_length=80)

    @model_validator(mode="after")
    def _require_item_unless_clear(self) -> "ApplyDescriptionMappingRequest":
        if self.clear:
            return self
        if not (self.category_name or "").strip():
            raise ValueError("category_name is required unless clear=true")
        if not (self.category_id or "").strip():
            raise ValueError("category_id is required unless clear=true")
        return self


class PayeeWrite(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    active: bool = True
    ref_num: str = Field("", max_length=80)
    currency: str = Field("TZS", max_length=12)

    @field_validator("name")
    @classmethod
    def normalize_payee_name(cls, value: str) -> str:
        return " ".join(value.upper().split())

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return " ".join((value or "TZS").upper().split()) or "TZS"


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


class SettingWrite(StrictModel):
    value: Any


class TransactionCreate(BaseModel):
    """Cashier register row create — mirrors desktop Transaction.to_doc() JSON.

    Extra keys (e.g. ``truck_sort_key``, accountant fields) are ignored so the
    desktop can POST its full document shape.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    date: datetime
    description: str = ""
    truck_number: str = ""
    amount: float = 0.0
    currency: str = "TZS"
    amount_usd: float | None = None
    category_id: str | None = None
    category_name: str | None = None
    category_confidence: float = 0.0
    item: str = ""
    lpo_do: str = ""
    do_number: str = ""
    memo: str = ""
    receipt_status: str = "pending"
    notes_flag: bool = False
    ref_float: str = ""
    ownership: str = ""
    approver: str = ""
    payee: str = ""
    cheque: str = ""
    reported_date: datetime | None = None
    cashier_id: str | None = None
    verified: bool = False
    verified_by: str | None = None
    verified_at: datetime | None = None
    rejection_reason: str | None = None
    rejected: bool = False
    discarded: bool = False
    deletion_requested: bool = False
    deletion_requested_at: datetime | None = None
    deletion_requested_by: str | None = None
    trashed: bool = False
    trashed_at: datetime | None = None
    trashed_by: str | None = None
    edited_after_verification: bool = False
    last_edited_at: datetime | None = None
    last_edited_by: str | None = None
    day_order: int | None = None
    register_status: str = "draft"
    month: str | None = None
    year: int | None = None
    possible_duplicate: bool = False
    duplicate_of_id: str | None = None
    daily_import_id: str | None = None
    daily_import_source: str | None = None
    date_discrepancy: bool = False
    import_primary_date: datetime | None = None
    original_transaction_id: str | None = None
    client_row_id: str | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None


class TransactionPatch(StrictModel):
    values: dict[str, Any]

    @field_validator("values")
    @classmethod
    def prevent_unsafe_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        blocked = {"_id", "created_at"}
        if blocked.intersection(value):
            raise ValueError("immutable field included")
        return value


class BulkTransactionPatchItem(StrictModel):
    id: str
    values: dict[str, Any] = Field(default_factory=dict)

    @field_validator("values")
    @classmethod
    def prevent_unsafe_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        blocked = {"_id", "created_at"}
        if blocked.intersection(value):
            raise ValueError("immutable field included")
        return value


class BulkTransactionPatchRequest(StrictModel):
    """Batch in-place patches for dirty register rows (one bulk_write)."""

    items: list[BulkTransactionPatchItem] = Field(
        default_factory=list, min_length=1, max_length=500
    )


class SubmitDayRequest(StrictModel):
    date: date


class DiscardDraftDayRequest(StrictModel):
    """Hard-delete every draft row for one register calendar day."""

    date: date
    cashier_id: str | None = None
    allow_any_cashier: bool = False


class DayOrderRequest(StrictModel):
    date: date
    ordered_ids: list[str] = Field(default_factory=list, max_length=2000)


class DuplicateCheckRequest(StrictModel):
    truck_number: str = ""
    amount: float = 0.0
    item: str = ""
    description: str = ""
    days: int = Field(5, ge=1, le=365)
    exclude_id: str | None = None


class DuplicateCheckCandidate(StrictModel):
    truck_number: str = ""
    amount: float = 0.0
    item: str = ""
    description: str = ""
    exclude_id: str | None = None


class DuplicateCheckBulkRequest(StrictModel):
    days: int = Field(5, ge=1, le=365)
    candidates: list[DuplicateCheckCandidate] = Field(
        default_factory=list, max_length=100
    )


class TransactionBulkCreateRequest(StrictModel):
    """Batch register creates — each item mirrors TransactionCreate."""

    model_config = ConfigDict(extra="forbid")

    items: list[TransactionCreate] = Field(default_factory=list, max_length=500)


class PendingEditRequest(StrictModel):
    updates: dict[str, Any] = Field(default_factory=dict)
    cashier_id: str | None = None

    @field_validator("updates")
    @classmethod
    def prevent_unsafe_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        blocked = {"_id", "created_at", "original_transaction_id"}
        if blocked.intersection(value):
            raise ValueError("immutable field included")
        return value


class RequestDeleteBody(StrictModel):
    cashier_id: str | None = None


class TransactionIdsRequest(StrictModel):
    ids: list[str] = Field(default_factory=list, max_length=500)
    cashier_id: str | None = None
    allow_any_cashier: bool = False


class ResubmitRejectedRequest(StrictModel):
    cashier_id: str | None = None
    updates_by_id: dict[str, dict[str, Any]] = Field(default_factory=dict)


class RejectRequest(StrictModel):
    reason: str = Field("", max_length=2000)


class BulkRejectRequest(StrictModel):
    ids: list[str] = Field(default_factory=list, max_length=100000)
    reason: str = Field("", max_length=2000)


class BulkIdsRequest(StrictModel):
    ids: list[str] = Field(default_factory=list, max_length=100000)


class CategoryAssignRequest(StrictModel):
    category_name: str = Field(min_length=1, max_length=200)
    category_id: str | None = None
    payee: str | None = None


class BulkCategoryAssignRequest(StrictModel):
    ids: list[str] = Field(default_factory=list, max_length=500)
    category_name: str = Field(min_length=1, max_length=200)
    category_id: str | None = None
    payee: str | None = None


class MasterPatchRequest(StrictModel):
    updates: dict[str, Any] = Field(default_factory=dict)


class BulkMasterUpdateRequest(StrictModel):
    ids: list[str] = Field(default_factory=list, max_length=500)
    updates: dict[str, Any] = Field(default_factory=dict)


class BulkMasterPatchItem(StrictModel):
    id: str
    updates: dict[str, Any] = Field(default_factory=dict)

    @field_validator("updates")
    @classmethod
    def prevent_unsafe_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        blocked = {"_id", "created_at", "verified", "trashed"}
        if blocked.intersection(value):
            raise ValueError("immutable field included")
        return value


class BulkMasterPatchRequest(StrictModel):
    """Per-row Master edits in one bulk_write (dirty Save)."""

    items: list[BulkMasterPatchItem] = Field(
        default_factory=list, min_length=1, max_length=500
    )


class CategoriesUsageTotalsRequest(StrictModel):
    names: list[str] = Field(default_factory=list, max_length=2000)


class DieselCashItemsRequest(StrictModel):
    names: list[str] = Field(default_factory=list, max_length=200)


class ReconExistingKeysRequest(StrictModel):
    keys: list[str] = Field(default_factory=list, max_length=5000)


class ReconRowWrite(StrictModel):
    station: str = ""
    schedule_period: str = ""
    sr_no: int | None = None
    sm_ref_no: str = ""
    prn_number: str = ""
    entry_reg_no: str = ""
    t1_no: str = ""
    t1_date: datetime | None = None
    importer: str = ""
    consignment: str = ""
    truck_and_trailer: str = ""
    charge: float = 0.0
    asycuda_amount: float | None = None
    exporter: str = ""
    confirmed: bool = False
    disputed: bool = False
    dispute_note: str = ""
    import_date: datetime | None = None
    upload_id: str = ""
    source_filename: str = ""


class ReconRowsCreateRequest(StrictModel):
    rows: list[ReconRowWrite] = Field(default_factory=list, max_length=5000)


class ReconRowStatusPatch(StrictModel):
    confirmed: bool | None = None
    disputed: bool | None = None
    dispute_note: str | None = None


class ReconStationsEnsureRequest(StrictModel):
    slugs: list[str] = Field(default_factory=list, max_length=500)


class ReconStationCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    border_post: str = Field("", max_length=200)


class DailyImportCommitRequest(StrictModel):
    """Staged daily Excel rows — field shapes vary; validated in import_ops."""

    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=20000)


class MasterImportCommitRequest(StrictModel):
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=20000)
    source_filename: str = Field("", max_length=500)
    skip_duplicates: bool = True


class ChartOfAccountsImportRequest(StrictModel):
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=20000)
    replace_existing: bool = True
    source_filename: str = Field("", max_length=500)


class PayeeImportRequest(StrictModel):
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=20000)
    replace_existing: bool = True
    source_filename: str = Field("", max_length=500)


class FeedExistingKeysRequest(StrictModel):
    keys: list[str] = Field(default_factory=list, max_length=10000)


class FeedImportRequest(StrictModel):
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=20000)


class ZambiaSheetLabelsRequest(StrictModel):
    labels: list[str] = Field(default_factory=list, max_length=500)


class ZambiaContentHashRequest(StrictModel):
    content_hash: str = Field("", max_length=128)
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=20000)


class DieselDupCheckRequest(StrictModel):
    content_hash: str = Field("", max_length=128)
    source_filename: str = Field("", max_length=500)
    sheet_label: str = Field("", max_length=200)
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=20000)


class SkippedImportSaveRequest(StrictModel):
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=20000)


class SkippedImportIdsRequest(StrictModel):
    ids: list[str] = Field(default_factory=list, max_length=5000)


class SkippedImportTruckPatch(StrictModel):
    truck_value: str = Field("", max_length=200)


class AuditEventWrite(StrictModel):

    action: str = Field(min_length=1, max_length=200)
    actor_id: str | None = Field(None, max_length=64)
    actor_username: str | None = Field(None, max_length=100)
    actor_role: str | None = Field(None, max_length=40)
    entity_type: str | None = Field(None, max_length=80)
    entity_ids: list[str] = Field(default_factory=list, max_length=500)
    upload_id: str | None = Field(None, max_length=120)
    outcome: str = Field("ok", max_length=40)
    error: str | None = Field(None, max_length=2000)
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = Field(None, max_length=64)
    client: str = Field("desktop", max_length=40)
    app_version: str | None = Field(None, max_length=40)

    @field_validator("entity_ids")
    @classmethod
    def normalize_entity_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            text = str(raw).strip()
            if text and text not in cleaned:
                cleaned.append(text[:64])
        return cleaned


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
