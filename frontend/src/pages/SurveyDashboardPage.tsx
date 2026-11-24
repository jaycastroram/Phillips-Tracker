import { useCallback, useEffect, useState } from 'react'
import { isImageReference } from '../utils'

type SurveyItemResult = {
  id: number
  item_name: string
  brand: string
  channel: string
  product_type: string
  key_program: string
  image_url: string
  price: string
  uom: string
  total_responses: number
  interested: number
  not_interested: number
  maybe: number
  avg_attention: number | null
  avg_engagement: number | null
  interest_pct: number
  respondent_emails: string[]
}

type SurveyResponse = {
  id: number
  email: string
  recommend_rollout: string
  feedback: string
  created_at: string
  item_name: string
  brand: string
}

type SurveySummary = {
  unique_respondents: number
  total_responses: number
  total_interested: number
  total_not_interested: number
}

type SurveyPeriodOption = {
  id: number
  name: string
  status: string
}

type SurveyResults = {
  summary: SurveySummary
  items: SurveyItemResult[]
  responses: SurveyResponse[]
  period?: SurveyPeriodOption | null
  periods?: SurveyPeriodOption[]
}

type Props = {
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>
  onExport: (periodId?: number | null) => void
}

type DashboardView = 'items' | 'respondents'
type ItemSort = 'interested' | 'not_interested' | 'total' | 'alpha'

function InterestBar({ interested, notInterested, maybe }: { interested: number; notInterested: number; maybe: number }) {
  const total = interested + notInterested + maybe
  if (total === 0) return <div className="sdb-bar sdb-bar--empty"><span>No responses yet</span></div>
  const iPct = Math.round((interested / total) * 100)
  const nPct = Math.round((notInterested / total) * 100)
  const mPct = 100 - iPct - nPct
  return (
    <div className="sdb-bar" title={`${iPct}% interested · ${nPct}% not interested · ${mPct}% maybe`}>
      {iPct > 0 && <div className="sdb-bar__yes" style={{ width: `${iPct}%` }}>{iPct >= 12 ? `${iPct}%` : ''}</div>}
      {mPct > 0 && <div className="sdb-bar__maybe" style={{ width: `${mPct}%` }}>{mPct >= 12 ? `${mPct}%` : ''}</div>}
      {nPct > 0 && <div className="sdb-bar__no" style={{ width: `${nPct}%` }}>{nPct >= 12 ? `${nPct}%` : ''}</div>}
    </div>
  )
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="sdb-stat-card">
      <span className="sdb-stat-label">{label}</span>
      <strong className="sdb-stat-value">{value}</strong>
      {sub && <span className="sdb-stat-sub">{sub}</span>}
    </div>
  )
}

export function SurveyDashboardPage({ apiFetch, onExport }: Props) {
  const [results, setResults] = useState<SurveyResults | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [view, setView] = useState<DashboardView>('items')
  const [sort, setSort] = useState<ItemSort>('interested')
  const [search, setSearch] = useState('')
  const [expandedItem, setExpandedItem] = useState<number | null>(null)
  const [selectedPeriodId, setSelectedPeriodId] = useState<number | null>(null)

  const load = useCallback(async (periodId?: number | null) => {
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams()
      if (periodId != null) params.set('period_id', String(periodId))
      const res = await apiFetch(`/admin/survey-results${params.toString() ? `?${params}` : ''}`)
      if (!res.ok) throw new Error('Unable to load survey results.')
      const data = await res.json()
      setResults(data)
      if (data.period?.id != null) setSelectedPeriodId(data.period.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load survey results.')
    } finally {
      setLoading(false)
    }
  }, [apiFetch])

  useEffect(() => {
    load(null)
  }, [load])

  if (loading && !results) return <div className="sdb-loading">Loading survey results...</div>
  if (error && !results) return <div className="error">{error}</div>
  if (!results) return null

  const { summary, items, responses, periods = [], period } = results

  const sortedItems = [...items]
    .filter((item) => {
      if (!search.trim()) return true
      const q = search.toLowerCase()
      return (
        item.item_name.toLowerCase().includes(q) ||
        item.brand.toLowerCase().includes(q) ||
        item.channel.toLowerCase().includes(q) ||
        item.product_type.toLowerCase().includes(q)
      )
    })
    .sort((a, b) => {
      if (sort === 'interested') return b.interested - a.interested || b.interest_pct - a.interest_pct
      if (sort === 'not_interested') return b.not_interested - a.not_interested
      if (sort === 'total') return b.total_responses - a.total_responses
      return a.item_name.localeCompare(b.item_name)
    })

  // Group responses by respondent email
  const respondentMap = new Map<string, SurveyResponse[]>()
  for (const r of responses) {
    if (!respondentMap.has(r.email)) respondentMap.set(r.email, [])
    respondentMap.get(r.email)!.push(r)
  }
  const respondents = [...respondentMap.entries()]
    .filter(([email]) => !search.trim() || email.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => b[1].length - a[1].length)

  const hasAnyResponses = summary.total_responses > 0

  return (
    <div className="sdb-root">
      <div className="sdb-period-bar">
        <label>
          Survey period
          <select
            value={selectedPeriodId ?? period?.id ?? ''}
            onChange={(event) => {
              const nextId = Number(event.target.value)
              setSelectedPeriodId(nextId)
              load(nextId)
            }}
          >
            {periods.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name} ({option.status})
              </option>
            ))}
          </select>
        </label>
        {period && (
          <span className={`survey-period-status survey-period-status--${period.status}`}>
            {period.status}
          </span>
        )}
      </div>

      {error && <div className="error">{error}</div>}

      {/* Summary cards */}
      <div className="sdb-summary-row">
        <StatCard label="Unique Respondents" value={summary.unique_respondents} />
        <StatCard
          label="Total Responses"
          value={summary.total_responses}
          sub={`across ${items.filter((i) => i.total_responses > 0).length} items`}
        />
        <StatCard
          label="Interested"
          value={summary.total_interested}
          sub={
            summary.total_responses
              ? `${Math.round((summary.total_interested / summary.total_responses) * 100)}% of responses`
              : undefined
          }
        />
        <StatCard
          label="Not Interested"
          value={summary.total_not_interested}
          sub={
            summary.total_responses
              ? `${Math.round((summary.total_not_interested / summary.total_responses) * 100)}% of responses`
              : undefined
          }
        />
        <StatCard
          label="Items with Votes"
          value={items.filter((i) => i.total_responses > 0).length}
          sub={`of ${items.length} total items`}
        />
      </div>

      {/* Top 3 items highlight */}
      {hasAnyResponses && (
        <div className="sdb-top-items">
          <h3 className="sdb-section-title">🏆 Top Items by Interest</h3>
          <div className="sdb-top-grid">
            {[...items]
              .filter((i) => i.interested > 0)
              .sort((a, b) => b.interested - a.interested || b.interest_pct - a.interest_pct)
              .slice(0, 3)
              .map((item, idx) => (
                <div key={item.id} className={`sdb-top-card sdb-top-card--${idx + 1}`}>
                  <span className="sdb-top-rank">#{idx + 1}</span>
                  {isImageReference(item.image_url) && (
                    <img className="sdb-top-image" src={item.image_url} alt="" />
                  )}
                  <div className="sdb-top-copy">
                    <strong>{item.item_name}</strong>
                    <span>{item.brand}</span>
                    <span className="sdb-top-votes">{item.interested} interested · {item.interest_pct}%</span>
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Toolbar */}
      <div className="sdb-toolbar">
        <div className="sdb-view-toggle">
          <button className={view === 'items' ? 'active' : ''} type="button" onClick={() => setView('items')}>
            By Item ({items.length})
          </button>
          <button className={view === 'respondents' ? 'active' : ''} type="button" onClick={() => setView('respondents')}>
            By Respondent ({summary.unique_respondents})
          </button>
        </div>
        <input
          className="sdb-search"
          placeholder={view === 'items' ? 'Search items...' : 'Search by email...'}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {view === 'items' && (
          <label className="sdb-sort-label">
            Sort by
            <select value={sort} onChange={(e) => setSort(e.target.value as ItemSort)}>
              <option value="interested">Most Interested</option>
              <option value="not_interested">Most Not Interested</option>
              <option value="total">Most Responses</option>
              <option value="alpha">A–Z</option>
            </select>
          </label>
        )}
        <button className="secondary-action" type="button" onClick={() => load(selectedPeriodId)}>
          Refresh
        </button>
        <button
          className="primary-action"
          type="button"
          onClick={() => onExport(selectedPeriodId)}
        >
          Export Excel
        </button>
      </div>

      {/* Items view */}
      {view === 'items' && (
        <div className="sdb-items-list">
          {sortedItems.length === 0 && (
            <div className="empty-state">{search ? 'No items match your search.' : 'No survey items found.'}</div>
          )}
          {sortedItems.map((item) => {
            const isExpanded = expandedItem === item.id
            return (
              <div key={item.id} className={`sdb-item-row ${item.total_responses === 0 ? 'sdb-item-row--empty' : ''}`}>
                <button
                  className="sdb-item-header"
                  type="button"
                  onClick={() => setExpandedItem(isExpanded ? null : item.id)}
                  aria-expanded={isExpanded}
                >
                  {isImageReference(item.image_url) && (
                    <img className="sdb-item-thumb" src={item.image_url} alt="" />
                  )}
                  <div className="sdb-item-meta">
                    <strong className="sdb-item-name">{item.item_name}</strong>
                    <span className="sdb-item-tags">
                      {[item.brand, item.channel, item.product_type].filter(Boolean).join(' · ')}
                    </span>
                  </div>
                  <div className="sdb-item-counts">
                    <span className="sdb-count-yes" title="Interested">✓ {item.interested}</span>
                    <span className="sdb-count-no" title="Not Interested">✗ {item.not_interested}</span>
                    {item.maybe > 0 && <span className="sdb-count-maybe" title="Maybe">? {item.maybe}</span>}
                    <span className="sdb-count-total">{item.total_responses} votes</span>
                  </div>
                  <div className="sdb-item-bar-wrapper">
                    <InterestBar
                      interested={item.interested}
                      notInterested={item.not_interested}
                      maybe={item.maybe}
                    />
                  </div>
                  <span className="sdb-item-chevron">{isExpanded ? '▲' : '▼'}</span>
                </button>

                {isExpanded && (
                  <div className="sdb-item-detail">
                    {item.total_responses === 0 ? (
                      <p className="muted">No responses yet for this item.</p>
                    ) : (
                      <>
                        <div className="sdb-detail-stats">
                          {item.avg_attention !== null && (
                            <div className="sdb-detail-stat">
                              <span>Avg. Attention</span>
                              <strong>{item.avg_attention}/5</strong>
                            </div>
                          )}
                          {item.avg_engagement !== null && (
                            <div className="sdb-detail-stat">
                              <span>Avg. Engagement</span>
                              <strong>{item.avg_engagement}/5</strong>
                            </div>
                          )}
                          <div className="sdb-detail-stat">
                            <span>Interest Rate</span>
                            <strong>{item.interest_pct}%</strong>
                          </div>
                        </div>

                        {/* Respondent feedback for this item */}
                        {responses
                          .filter((r) => r.item_name === item.item_name && r.feedback?.trim())
                          .map((r) => (
                            <div key={r.id} className="sdb-feedback-row">
                              <span className={`sdb-feedback-badge sdb-feedback-badge--${r.recommend_rollout === 'Yes' ? 'yes' : r.recommend_rollout === 'No' ? 'no' : 'maybe'}`}>
                                {r.recommend_rollout === 'Yes' ? 'Interested' : r.recommend_rollout === 'No' ? 'Not Interested' : 'Maybe'}
                              </span>
                              <span className="sdb-feedback-email">{r.email}</span>
                              <p className="sdb-feedback-text">"{r.feedback}"</p>
                            </div>
                          ))}

                        {/* Respondent emails */}
                        {item.respondent_emails.length > 0 && (
                          <details className="sdb-respondents-detail">
                            <summary>{item.respondent_emails.length} respondent{item.respondent_emails.length !== 1 ? 's' : ''}</summary>
                            <div className="sdb-respondent-chips">
                              {item.respondent_emails.map((email) => (
                                <span key={email} className="sdb-respondent-chip">{email}</span>
                              ))}
                            </div>
                          </details>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Respondents view */}
      {view === 'respondents' && (
        <div className="sdb-respondents-list">
          {respondents.length === 0 && (
            <div className="empty-state">{search ? 'No respondents match your search.' : 'No survey responses yet.'}</div>
          )}
          {respondents.map(([email, resps]) => {
            const interestedCount = resps.filter((r) => r.recommend_rollout === 'Yes').length
            const notInterestedCount = resps.filter((r) => r.recommend_rollout === 'No').length
            return (
              <div key={email} className="sdb-respondent-row">
                <div className="sdb-respondent-header">
                  <strong className="sdb-respondent-email">{email}</strong>
                  <div className="sdb-item-counts">
                    <span className="sdb-count-yes">✓ {interestedCount}</span>
                    <span className="sdb-count-no">✗ {notInterestedCount}</span>
                    <span className="sdb-count-total">{resps.length} response{resps.length !== 1 ? 's' : ''}</span>
                  </div>
                  <span className="muted" style={{ fontSize: '0.8rem' }}>
                    Last: {new Date(resps[0].created_at).toLocaleDateString()}
                  </span>
                </div>
                <div className="sdb-respondent-items">
                  {resps.map((r) => (
                    <div key={r.id} className="sdb-respondent-item">
                      <span className={`sdb-feedback-badge sdb-feedback-badge--${r.recommend_rollout === 'Yes' ? 'yes' : r.recommend_rollout === 'No' ? 'no' : 'maybe'}`}>
                        {r.recommend_rollout === 'Yes' ? 'Interested' : r.recommend_rollout === 'No' ? 'Not Interested' : 'Maybe'}
                      </span>
                      <span className="sdb-respondent-item-name">{r.item_name}</span>
                      {r.feedback?.trim() && <p className="sdb-feedback-text">"{r.feedback}"</p>}
                    </div>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
