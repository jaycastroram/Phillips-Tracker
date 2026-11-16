import { useCallback, useMemo, useState } from 'react'
import type { AppUser, CompanyInfo } from '../types'
import {
  resolveWorkingCompanyId,
  withCompanyQuery,
  type AdminCompanyFilter,
} from '../utils/adminCompany'
import { BuyBookLayoutPage } from './BuyBookLayoutPage'
import { SurveyDashboardPage } from './SurveyDashboardPage'
import { SurveyPeriodsPage } from './SurveyPeriodsPage'

type SurveysTab = 'manage' | 'buy-book' | 'results'

type Props = {
  currentUser: AppUser
  companies: CompanyInfo[]
  companyFilter: AdminCompanyFilter
  onCompanyFilterChange: (value: AdminCompanyFilter) => void
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>
  onDownloadSurveyResponses: (periodId?: number | null) => void
}

export function SurveysPage({
  currentUser,
  companies,
  companyFilter,
  onCompanyFilterChange,
  apiFetch,
  onDownloadSurveyResponses,
}: Props) {
  const [tab, setTab] = useState<SurveysTab>('manage')
  const isAdmin = currentUser.role === 'admin'

  const workingCompanyId = useMemo(
    () =>
      resolveWorkingCompanyId(
        companyFilter,
        companies.map((company) => company.id),
        currentUser.company_id,
      ),
    [companyFilter, companies, currentUser.company_id],
  )

  const scopedApiFetch = useCallback(
    (path: string, options?: RequestInit) => {
      if (!isAdmin || workingCompanyId == null) return apiFetch(path, options)
      return apiFetch(withCompanyQuery(path, workingCompanyId), options)
    },
    [apiFetch, isAdmin, workingCompanyId],
  )

  return (
    <section className="surveys-page">
      {isAdmin && (
        <div className="admin-company-filter">
          <label>
            Company
            <select
              value={companyFilter === 'all' ? 'all' : String(companyFilter)}
              onChange={(e) => {
                const value = e.target.value
                onCompanyFilterChange(value === 'all' ? 'all' : Number(value))
              }}
            >
              <option value="all">All companies (uses first / home company for edits)</option>
              {companies.map((company) => (
                <option key={company.id} value={company.id}>
                  {company.company_name || company.name}
                </option>
              ))}
            </select>
          </label>
          <span className="admin-company-filter-note">
            Admins can create and edit surveys and buy books for any company. Editors stay locked to theirs.
          </span>
        </div>
      )}

      <div className="admin-tabs" role="tablist" aria-label="Survey sections">
        <button
          className={tab === 'manage' ? 'active' : ''}
          type="button"
          onClick={() => setTab('manage')}
        >
          Create / Manage
        </button>
        <button
          className={tab === 'buy-book' ? 'active' : ''}
          type="button"
          onClick={() => setTab('buy-book')}
        >
          Buy Book Layout
        </button>
        <button
          className={tab === 'results' ? 'active' : ''}
          type="button"
          onClick={() => setTab('results')}
        >
          Results
        </button>
      </div>

      <div key={workingCompanyId ?? 'home'}>
        {tab === 'manage' && <SurveyPeriodsPage apiFetch={scopedApiFetch} />}
        {tab === 'buy-book' && <BuyBookLayoutPage apiFetch={scopedApiFetch} />}
        {tab === 'results' && (
          <SurveyDashboardPage apiFetch={scopedApiFetch} onExport={onDownloadSurveyResponses} />
        )}
      </div>
    </section>
  )
}
