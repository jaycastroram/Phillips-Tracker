import { useState } from 'react'
import { SurveyDashboardPage } from './SurveyDashboardPage'
import { SurveyPeriodsPage } from './SurveyPeriodsPage'

type SurveysTab = 'manage' | 'results'

type Props = {
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>
  onDownloadSurveyResponses: (periodId?: number | null) => void
}

export function SurveysPage({ apiFetch, onDownloadSurveyResponses }: Props) {
  const [tab, setTab] = useState<SurveysTab>('manage')

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
        <button
          className={tab === 'results' ? 'active' : ''}
          type="button"
          onClick={() => setTab('results')}
        >
          Results
        </button>
      </div>

      {tab === 'manage' && <SurveyPeriodsPage apiFetch={apiFetch} />}
      {tab === 'results' && (
        <SurveyDashboardPage apiFetch={apiFetch} onExport={onDownloadSurveyResponses} />
      )}
    </section>
  )
}
