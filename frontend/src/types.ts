export type SheetKey = 'ad-hoc' | 'buys' | 'completed'
export type StatusTargetSheet = SheetKey | 'all'
export type Role = 'admin' | 'editor'
export type Page = 'tracker' | 'admin-users' | 'system-log' | 'surveys'
export type AdminSettingsTab = 'users' | 'companies' | 'statuses' | 'kanban'
export type SurveyFilterField = 'brand' | 'channel' | 'product_type' | 'key_program'
export type RowPageSize = 10 | 20 | 30 | 'all'
export type SortDirection = 'asc' | 'desc'
export type ViewerStatus = 'on-track' | 'not-on-track'
export type SurveyInterest = '' | 'Interested' | 'Not Interested'

export type SheetMeta = {
  label: string
  dateLabel: string
  statuses: string[]
}

export type TrackerItem = {
  id: number
  sheet: SheetKey
  source_row?: number
  company_id?: number | null
  company_name?: string
  company_slug?: string
  date_or_buy: string
  current_status: string
  visual_reference: string
  netsuite_item_id?: string
  brand: string
  program_name: string
  item_name: string
  qty: string
  important_notes: string
  mrl_order_number: string
  estimated_ship_date: string
  estimated_ihd: string
  tracking: string
  extra?: Record<string, string>
  updated_at: string
}

export type EditableField = Exclude<
  keyof TrackerItem,
  'id' | 'sheet' | 'source_row' | 'extra' | 'updated_at' | 'company_id' | 'company_name' | 'company_slug'
>

export type ColumnKey = EditableField | 'actions'
export type ItemDraft = Record<EditableField, string>
export type PendingUpdates = Record<number, Partial<ItemDraft>>

export type AuditLog = {
  id: number
  user_id: number | null
  user_email: string
  action: string
  sheet: string | null
  item_id: number | null
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  created_at: string
}

export type KanbanColumn = {
  id: number
  title: string
  statuses: string[]
  sort_order: number
  is_visible: boolean
  created_at: string
  updated_at: string
}

export type SheetStatus = {
  id: number
  sheet: SheetKey
  status: string
  sort_order: number
  created_at: string
  updated_at: string
}

export type SurveyItem = {
  id: number
  item_name: string
  brand: string
  channel: string
  product_type: string
  key_program: string
  item_description: string
  uom: string
  price: string
  image_url: string
  sort_order: number
  is_active: boolean
  in_survey: boolean
  in_buy_book: boolean
  survey_sort_order: number
  buy_book_sort_order?: number
  response_width: number
  page_break_before: boolean
  buy_book_page_break_before?: boolean
  created_at: string
  updated_at: string
  period_item_id?: number | null
  catalog_item_id?: number
  image_display_size?: 'small' | 'medium' | 'large' | 'full'
  image_fit?: 'contain' | 'cover'
  // NetSuite sync facts (admin payload only): the row carries a snapshot, and
  // the name still needs a person.
  netsuite_synced?: boolean
  needs_name?: boolean
  group_field?: SurveyFilterField | null
  group_label?: string | null
  group_headers?: { field: SurveyFilterField; label: string; level?: number }[]
}

export type SurveyPeriodStatus = 'draft' | 'open' | 'closed'

export type SurveyQuestionType = 'yes_no' | 'single_choice' | 'dropdown' | 'checkbox' | 'text' | 'scale'

export type SurveyQuestion = {
  id: string
  type: SurveyQuestionType
  label: string
  required: boolean
  options: string[]
}

export type SurveyPeriod = {
  id: number
  company_id: number
  name: string
  status: SurveyPeriodStatus
  opens_at: string | null
  closes_at: string | null
  filter_fields: SurveyFilterField[]
  questions?: SurveyQuestion[]
  item_count?: number
  created_at: string
  updated_at: string
}

export type SurveyAnswerValue = string | number | string[]

export type SurveyResponseDraft = {
  interest: SurveyInterest
  notes: string
  answers: Record<string, SurveyAnswerValue>
}

export type CompanyInfo = {
  id: number
  company_slug: string
  company_name: string
  slug?: string
  name?: string
  logo_url: string
  banner_url: string
  is_active?: boolean
}

export type AppUser = {
  id: number
  email: string
  name: string
  role: Role
  is_active: boolean
  company_id: number | null
  company?: CompanyInfo | null
  created_at: string
  updated_at: string
}
