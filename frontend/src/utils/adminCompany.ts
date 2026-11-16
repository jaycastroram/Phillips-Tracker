export const ADMIN_COMPANY_FILTER_KEY = 'mrl-admin-company-filter'

export type AdminCompanyFilter = number | 'all'

export function readAdminCompanyFilter(fallback: AdminCompanyFilter = 'all'): AdminCompanyFilter {
  const raw = window.localStorage.getItem(ADMIN_COMPANY_FILTER_KEY)
  if (raw === 'all') return 'all'
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

export function writeAdminCompanyFilter(value: AdminCompanyFilter) {
  window.localStorage.setItem(ADMIN_COMPANY_FILTER_KEY, String(value))
}

export function withCompanyQuery(path: string, companyId: number) {
  const [base, hash = ''] = path.split('#')
  const separator = base.includes('?') ? '&' : '?'
  return `${base}${separator}company_id=${companyId}${hash ? `#${hash}` : ''}`
}

export function resolveWorkingCompanyId(
  filter: AdminCompanyFilter,
  companyIds: number[],
  fallback: number | null,
): number | null {
  if (typeof filter === 'number') return filter
  return companyIds[0] ?? fallback
}
