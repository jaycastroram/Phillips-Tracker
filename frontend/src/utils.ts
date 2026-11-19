import { DEFAULT_COMPANY_SLUG, NOT_ON_TRACK_STATUSES } from './constants'
import type { SheetKey, TrackerItem, ViewerStatus } from './types'

export type PublicCompanyPage = 'viewer' | 'survey' | 'book' | 'buy-book'
export type PublicPagePath = '/viewer' | '/viewer/kanban' | '/survey' | '/buy-book'

export type PublicRoute = {
  companySlug: string
  page: PublicCompanyPage | null
  isKanban: boolean
  usesCompanyPrefix: boolean
  /** Only for page === 'book': the token in /book/<token>. */
  bookToken?: string
}

export function parsePublicRoute(pathname: string): PublicRoute {
  const [first = '', second = '', third = ''] = pathname.split('/').filter(Boolean)

  if (first === 'book') {
    return {
      companySlug: DEFAULT_COMPANY_SLUG,
      page: 'book',
      isKanban: false,
      usesCompanyPrefix: false,
      bookToken: second,
    }
  }

  // The buy book's legacy paths: /buy-book and /phillips/buy-book, plus the
  // no-hyphen spelling /buybook that a customer site may have embedded. /book/
  // <token> above is the separate token page and is checked first.
  const buyBookFirst = first === 'buy-book' || first === 'buybook'
  if (buyBookFirst) {
    // /buybook/<company-slug> was asked for by hand; treat its tail as the
    // company rather than silently showing the default company's book.
    if (second) {
      return {
        companySlug: second.toLowerCase(),
        page: 'buy-book',
        isKanban: false,
        usesCompanyPrefix: true,
      }
    }
    return {
      companySlug: DEFAULT_COMPANY_SLUG,
      page: 'buy-book',
      isKanban: false,
      usesCompanyPrefix: false,
    }
  }

  if (first === 'viewer' || first === 'survey') {
    return {
      companySlug: DEFAULT_COMPANY_SLUG,
      page: first,
      isKanban: first === 'viewer' && second === 'kanban',
      usesCompanyPrefix: false,
    }
  }

  const buyBookSecond = second === 'buy-book' || second === 'buybook'
  if (second === 'viewer' || second === 'survey' || buyBookSecond) {
    return {
      companySlug: first.toLowerCase(),
      page: buyBookSecond ? 'buy-book' : (second as PublicCompanyPage),
      isKanban: second === 'viewer' && third === 'kanban',
      usesCompanyPrefix: true,
    }
  }

  return {
    companySlug: DEFAULT_COMPANY_SLUG,
    page: null,
    isKanban: false,
    usesCompanyPrefix: false,
  }
}

export function publicPagePath(route: PublicRoute, page: PublicPagePath) {
  return route.usesCompanyPrefix ? `/${route.companySlug}${page}` : page
}

export function companyPublicApiPath(companySlug: string, resource: string) {
  return `/public/${encodeURIComponent(companySlug)}/${resource}`
}

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
