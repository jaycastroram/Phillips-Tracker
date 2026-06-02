import { NOT_ON_TRACK_STATUSES } from './constants'
import type { SheetKey, TrackerItem, ViewerStatus } from './types'

export function isImageReference(value: string) {
  if (!value.trim()) return false

  try {
    const url = new URL(value)
    return (
      url.hostname.includes('res.cloudinary.com') ||
      /\.(avif|gif|jpe?g|png|webp)$/i.test(url.pathname)
    )
  } catch {
    return false
  }
}

export function getViewerStatus(item: TrackerItem): ViewerStatus {
  return NOT_ON_TRACK_STATUSES.has(item.current_status) ? 'not-on-track' : 'on-track'
}

export function viewerStatusLabel(status: ViewerStatus) {
  return status === 'on-track' ? 'On Track' : 'Not On Track'
}

export function publicSheetLabel(sheet: SheetKey) {
  if (sheet === 'ad-hoc') return 'Ad Hoc'
  if (sheet === 'buys') return 'Buys'
  return 'Completed'
}

export function escapePrintValue(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}
