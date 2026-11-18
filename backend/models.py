from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

Role = Literal["admin", "editor"]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ItemCreate(BaseModel):
    sheet: str = "ad-hoc"
    date_or_buy: str = ""
    current_status: str = ""
    visual_reference: str = ""
    netsuite_item_id: str = ""
    brand: str = ""
    program_name: str = ""
    item_name: str = ""
    qty: str = ""
    important_notes: str = ""
    mrl_order_number: str = ""
    estimated_ship_date: str = ""
    estimated_ihd: str = ""
    tracking: str = ""


class ItemUpdate(BaseModel):
    date_or_buy: str | None = None
    current_status: str | None = None
    visual_reference: str | None = None
    netsuite_item_id: str | None = None
    brand: str | None = None
    program_name: str | None = None
    item_name: str | None = None
    qty: str | None = None
    important_notes: str | None = None
    mrl_order_number: str | None = None
    estimated_ship_date: str | None = None
    estimated_ihd: str | None = None
    tracking: str | None = None


class ItemBatchUpdate(BaseModel):
    id: int
    changes: ItemUpdate


class ItemChangeBatch(BaseModel):
    creates: list[ItemCreate] = []
    updates: list[ItemBatchUpdate] = []
    deletes: list[int] = []


class UserCreate(BaseModel):
    email: EmailStr
    name: str = ""
    password: str = Field(min_length=6)
    role: Role = "editor"
    is_active: bool = True
    company_id: int | None = None


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    name: str | None = None
    role: Role | None = None
    is_active: bool | None = None
    company_id: int | None = None


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    logo_url: str | None = None
    banner_url: str | None = None
    is_active: bool | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=6)


class KanbanColumnCreate(BaseModel):
    title: str = Field(min_length=1)
    statuses: list[str] = []
    sort_order: int = 0
    is_visible: bool = True


class KanbanColumnUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    statuses: list[str] | None = None
    sort_order: int | None = None
    is_visible: bool | None = None


class SheetStatusCreate(BaseModel):
    sheet: str
    status: str = Field(min_length=1)


class SurveyQuestion(BaseModel):
    id: str = ""
    type: Literal["yes_no", "single_choice", "text", "scale"]
    label: str = Field(min_length=1, max_length=240)
    required: bool = False
    options: list[str] = []


class SurveyResponseCreate(BaseModel):
    survey_item_id: int
    email: EmailStr
    answers: dict[str, str | int | float] = {}
    # Legacy fields kept optional for older clients / mirrored answers.
    attention_effectiveness: int = Field(default=3, ge=1, le=5)
    recommend_rollout: Literal["Yes", "No", "Maybe"] = "Maybe"
    retail_engagement: int = Field(default=3, ge=1, le=5)
    stands_out: Literal["Yes", "No", "Neutral"] = "Neutral"
    price_reasonable: Literal["Yes", "No"] = "Yes"
    feedback: str = ""


class SurveyLayoutItemUpdate(BaseModel):
    id: int
    in_survey: bool
    in_buy_book: bool
    response_width: int = Field(ge=20, le=70)
    page_break_before: bool = False
    image_display_size: Literal["small", "medium", "large", "full"] = "medium"
    image_fit: Literal["contain", "cover"] = "contain"


class SurveyLayoutUpdate(BaseModel):
    filter_fields: list[str] = []
    items: list[SurveyLayoutItemUpdate] = []


class BuyBookLayoutItemUpdate(BaseModel):
    id: int
    in_buy_book: bool = True
    page_break_before: bool = False
    image_display_size: Literal["small", "medium", "large", "full"] = "medium"
    image_fit: Literal["contain", "cover"] = "contain"


class BuyBookLayoutUpdate(BaseModel):
    filter_fields: list[str] = []
    group_by: Literal["brand", "channel", "key_program", "product_type", "none"] = "brand"
    items: list[BuyBookLayoutItemUpdate] = []


class SurveyPeriodCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    copy_from_period_id: int | None = None
    open_immediately: bool = False


class SurveyPeriodUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["draft", "open", "closed"] | None = None
    opens_at: str | None = None
    closes_at: str | None = None


class SurveyPeriodItemUpdate(BaseModel):
    catalog_item_id: int
    in_survey: bool = True
    response_width: int = Field(ge=20, le=70)
    page_break_before: bool = False
    image_display_size: Literal["small", "medium", "large", "full"] = "medium"
    image_fit: Literal["contain", "cover"] = "contain"


class SurveyPeriodLayoutUpdate(BaseModel):
    filter_fields: list[str] = []
    questions: list[SurveyQuestion] = []
    items: list[SurveyPeriodItemUpdate] = []
