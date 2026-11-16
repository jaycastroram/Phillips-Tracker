import { useCallback, useEffect, useState } from 'react'
import { BASE_COLUMNS } from '../constants'
import type { AuditLog, CompanyInfo } from '../types'
import {
  readAdminCompanyFilter,
  writeAdminCompanyFilter,
  type AdminCompanyFilter,
} from '../utils/adminCompany'

type SystemLogPageProps = {
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>
}

function auditLogSummary(log: AuditLog): string {
  if (log.action === 'update' && log.before && log.after) {
    const changes = BASE_COLUMNS.flatMap((col) => {
      const before = String(log.before?.[col.field] ?? '')
      const after = String(log.after?.[col.field] ?? '')
      return before === after ? [] : [`${col.label}: "${before}" → "${after}"`]
    })
    return changes.length ? changes.join('; ') : 'Updated tracker row'
  }
  const itemName = String(log.after?.item_name ?? log.before?.item_name ?? '').trim()
  const programName = String(log.after?.program_name ?? log.before?.program_name ?? '').trim()
  const label = [programName, itemName].filter(Boolean).join(' – ')
  if (log.action === 'create') return label ? `Created ${label}` : 'Created tracker row'
  if (log.action === 'delete') return label ? `Deleted ${label}` : 'Deleted tracker row'
  return 'Tracker row changed'
}

export function SystemLogPage({ apiFetch }: SystemLogPageProps) {
  const [logs, setLogs] = useState<AuditLog[]>([])
  const [companies, setCompanies] = useState<CompanyInfo[]>([])
  const [companyFilter, setCompanyFilter] = useState<AdminCompanyFilter>(() => readAdminCompanyFilter('all'))
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadCompanies = useCallback(async () => {
    try {
      const res = await apiFetch('/admin/companies')
      if (!res.ok) return
      setCompanies((await res.json()).companies || [])
    } catch {
      /* ignore — log page can still load without company names */
    }
  }, [apiFetch])

  const loadLogs = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const path = typeof companyFilter === 'number'
        ? `/admin/audit-logs?company_id=${companyFilter}`
        : '/admin/audit-logs'
      const res = await apiFetch(path)
      if (!res.ok) throw new Error('Unable to load system log.')
      const data = await res.json()
      setLogs(data.logs)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load system log.')
    } finally {
      setLoading(false)
    }
  }, [apiFetch, companyFilter])

  useEffect(() => { loadCompanies() }, [loadCompanies])
  useEffect(() => { loadLogs() }, [loadLogs])

  return (
    <>
      {error && <div className="error">{error}</div>}
      <div className="admin-company-filter">
        <label>
          Company
          <select
            value={companyFilter === 'all' ? 'all' : String(companyFilter)}
            onChange={(e) => {
              const value = e.target.value
              const next: AdminCompanyFilter = value === 'all' ? 'all' : Number(value)
              setCompanyFilter(next)
              writeAdminCompanyFilter(next)
            }}
          >
            <option value="all">All companies</option>
            {companies.map((company) => (
              <option key={company.id} value={company.id}>
                {company.company_name || company.name}
              </option>
            ))}
          </select>
        </label>
        <span className="admin-company-filter-note">
          Filters audit entries by company. Shared with Admin Settings and Surveys.
        </span>
      </div>
      <section className="table-card audit-log-card">
        <div className="table-tools">
          <span>{loading ? 'Loading system log...' : `${logs.length} log entries`}</span>
          <button type="button" onClick={loadLogs}>Refresh</button>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>User</th>
                <th>Action</th>
                <th>Sheet</th>
                <th>Item</th>
                <th>Summary</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.id}>
                  <td>{new Date(log.created_at).toLocaleString()}</td>
                  <td>{log.user_email}</td>
                  <td>{log.action}</td>
                  <td>{log.sheet || '—'}</td>
                  <td>{log.item_id ?? '—'}</td>
                  <td>{auditLogSummary(log)}</td>
                </tr>
              ))}
              {!loading && logs.length === 0 && (
                <tr>
                  <td colSpan={6} className="empty-state">No system log entries yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  )
}
