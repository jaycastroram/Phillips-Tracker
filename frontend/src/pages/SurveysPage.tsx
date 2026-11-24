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
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>
  onDownloadSurveyResponses: (periodId?: number | null) => void
}

export function SurveysPage({
  currentUser,
  companies,
  companyFilter,
  apiFetch,
  onDownloadSurveyResponses,
}: Props) {
  const [tab, setTab] = useState<SurveysTab>('manage')
  const isAdmin = currentUser.role === 'admin'

  // The survey and the book are Phillips-only for now. With the filter on
  // "All companies" this page still reads and writes one company, so it picks
  // Phillips rather than whichever company happens to be first.
  const phillips = useMemo(
    () =>
      companies.find((company) => (company.company_slug || company.slug || '') === 'phillips') ??
      companies.find((company) => (company.company_name || company.name || '').toLowerCase().includes('phillips')),
    [companies],
  )

  const workingCompanyId = useMemo(
    () =>
      resolveWorkingCompanyId(
        companyFilter === 'all' && phillips ? phillips.id : companyFilter,
        companies.map((company) => company.id),
        phillips?.id ?? currentUser.company_id,
      ),
    [companyFilter, companies, currentUser.company_id, phillips],
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
      <div className="admin-tabs" role="tablist" aria-label="Survey sections">
        <button
          className={tab === 'manage' ? 'active' : ''}
          type="button"
          onClick={() => setTab('manage')}
        >
          Create / Manage
        </button>
        {isAdmin && (
          <button
            className={tab === 'buy-book' ? 'active' : ''}
            type="button"
            onClick={() => setTab('buy-book')}
          >
            Buy Book Layout
          </button>
        )}
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
        {tab === 'buy-book' && (
          <BuyBookLayoutPage
            apiFetch={scopedApiFetch}
            companyId={workingCompanyId}
            isAdmin={isAdmin}
          />
        )}
        {tab === 'results' && (
          <SurveyDashboardPage apiFetch={scopedApiFetch} onExport={onDownloadSurveyResponses} />
        )}
      </div>
    </section>
  )
}
