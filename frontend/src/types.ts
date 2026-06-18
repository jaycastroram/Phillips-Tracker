export type SheetKey = 'ad-hoc' | 'buys' | 'completed'
export type StatusTargetSheet = SheetKey | 'all'
export type Role = 'admin' | 'editor'
export type Page = 'tracker' | 'admin-users' | 'system-log'
export type AdminSettingsTab = 'users' | 'statuses' | 'kanban'
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
  date_or_buy: string
  current_status: string
  visual_reference: string
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
  'id' | 'sheet' | 'source_row' | 'extra' | 'updated_at'
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
  created_at: string
  updated_at: string
}

export type SurveyResponseDraft = {
  interest: SurveyInterest
  notes: string
}

export type AppUser = {
  id: number
  email: string
  name: string
  role: Role
  is_active: boolean
  created_at: string
  updated_at: string
}
