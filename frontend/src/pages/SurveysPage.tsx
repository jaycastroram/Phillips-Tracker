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

  const workingCompanyId = useMemo(
    () =>
      resolveWorkingCompanyId(
        companyFilter,
        companies.map((company) => company.id),
        currentUser.company_id,
      ),
    [companyFilter, companies, currentUser.company_id],
  )

  // The public link is addressed by the company's slug, so the share panel
  // needs the company the rest of the page is already scoped to.
  const workingCompany = useMemo(
    () =>
      companies.find((company) => company.id === workingCompanyId) ??
      companies.find((company) => company.id === currentUser.company_id) ??
      null,
    [companies, currentUser.company_id, workingCompanyId],
  )
  const companySlug = workingCompany?.company_slug ?? workingCompany?.slug ?? 'phillips'

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
        {tab === 'manage' && <SurveyPeriodsPage apiFetch={scopedApiFetch} companySlug={companySlug} />}
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
