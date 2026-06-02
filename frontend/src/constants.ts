import type {
  ColumnKey,
  EditableField,
  ItemDraft,
  SheetKey,
  SurveyResponseDraft,
} from './types'

export const API_BASE = '/api'
export const AUTH_STORAGE_KEY = 'phillips-tracker-auth-token'
export const SHEET_ORDER: SheetKey[] = ['ad-hoc', 'buys', 'completed']
export const COLUMN_WIDTH_STORAGE_KEY = 'phillips-tracker-column-widths'
export const NOT_ON_TRACK_STATUSES = new Set([
  'Details Needed',
  'Quoting',
  'Pending Feedback',
  'ON HOLD',
  'Canceled',
])

export const BASE_COLUMNS: { field: EditableField; label: string; width: number; className?: string }[] = [
  { field: 'date_or_buy', label: 'Date/Buy', width: 140 },
  { field: 'current_status', label: 'Current Status', width: 190 },
  { field: 'visual_reference', label: 'Visual Reference', width: 170 },
  { field: 'brand', label: 'Brand', width: 150 },
  { field: 'program_name', label: 'Program Name', width: 300, className: 'wide' },
  { field: 'item_name', label: 'Item Name', width: 300, className: 'wide' },
  { field: 'qty', label: 'Qty', width: 110 },
  { field: 'important_notes', label: 'Important Notes', width: 360, className: 'notes' },
  { field: 'mrl_order_number', label: 'MRL Order #', width: 150 },
  { field: 'estimated_ship_date', label: 'Estimated Ship Date', width: 190 },
  { field: 'estimated_ihd', label: 'Estimated IHD', width: 170 },
  { field: 'tracking', label: 'Tracking', width: 340, className: 'notes' },
]

export const DEFAULT_COLUMN_WIDTHS: Record<ColumnKey, number> = {
  ...Object.fromEntries(BASE_COLUMNS.map((column) => [column.field, column.width])),
  actions: 110,
} as Record<ColumnKey, number>

export const EMPTY_ITEM_DRAFT: ItemDraft = {
  date_or_buy: '',
  current_status: '',
  visual_reference: '',
  brand: '',
  program_name: '',
  item_name: '',
  qty: '',
  important_notes: '',
  mrl_order_number: '',
  estimated_ship_date: '',
  estimated_ihd: '',
  tracking: '',
}

export const EMPTY_SURVEY_RESPONSE: SurveyResponseDraft = {
  interest: '',
  notes: '',
}
