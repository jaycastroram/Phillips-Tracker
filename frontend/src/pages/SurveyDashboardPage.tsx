import { useCallback, useEffect, useState } from 'react'
import type { SurveyQuestionType } from '../types'
import { isImageReference } from '../utils'

type ChoiceTally = { option: string; count: number }

type QuestionTally = {
  question_id: string
  label: string
  type: SurveyQuestionType
  answered: number
  choices: ChoiceTally[]
  average: number | null
  answers: { email: string; answer: string; submitted_at: string }[]
}

type ItemResult = {
  id: number
  item_name: string
  item_number: string
  cost: string
  image_url: string
  item_description: string
  sort_order: number
  response_count: number
  questions: QuestionTally[]
}

type RespondentRow = {
  id: number
  email: string
  created_at: string
  items_answered: number
}

type SurveySummary = {
  submissions: number
  unique_respondents: number
  items: number
  answered_items: number
  total_answers: number
}

type PeriodOption = { id: number; name: string; status: string }

type SurveyResults = {
  summary: SurveySummary
  items: ItemResult[]
  respondents: RespondentRow[]
  period?: PeriodOption | null
  periods?: PeriodOption[]
}

type Props = {
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>
  onExport: (periodId?: number | null) => void
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

function ChoiceBars({ choices, total }: { choices: ChoiceTally[]; total: number }) {
  return (
    <ul className="sdb-choice-list">
      {choices.map((choice) => {
        const width = total > 0 ? Math.round((choice.count / total) * 100) : 0
        return (
          <li key={choice.option}>
            <span className="sdb-choice-label">{choice.option || '(blank)'}</span>
            <span className="sdb-choice-bar">
              <span style={{ width: `${width}%` }} />
            </span>
            <span className="sdb-choice-count">
              {choice.count}
              {total > 0 ? <em>{width}%</em> : null}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

function QuestionTallyBlock({ tally }: { tally: QuestionTally }) {
  if (tally.type === 'text') {
    return (
      <div className="sdb-tally">
        <div className="sdb-tally-head">
          <strong>{tally.label}</strong>
          <span>{tally.answered} answered</span>
        </div>
        {tally.answers.length === 0 ? (
          <p className="muted">No answers yet.</p>
        ) : (
          <ul className="sdb-text-answers">
            {tally.answers.map((answer, index) => (
              <li key={`${answer.email}-${index}`}>
                <p>{answer.answer}</p>
                <span>{answer.email || 'Anonymous'}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    )
  }

  if (tally.type === 'scale') {
    return (
      <div className="sdb-tally">
        <div className="sdb-tally-head">
          <strong>{tally.label}</strong>
          <span>
            {tally.average === null ? 'No average yet' : `Average ${tally.average} / 5`}
          </span>
        </div>
        <ChoiceBars choices={tally.choices} total={tally.answered} />
      </div>
    )
  }

  return (
    <div className="sdb-tally">
      <div className="sdb-tally-head">
        <strong>{tally.label}</strong>
        <span>
          {tally.answered} {tally.answered === 1 ? 'answer' : 'answers'}
        </span>
      </div>
      <ChoiceBars choices={tally.choices} total={tally.answered} />
    </div>
  )
}

/**
 * Results, per item.
 *
 * A survey's items ask different questions, so there is no single tally for the
 * survey: each item shows its own response count and the tallies of the
 * questions it asked. The export prints the same rows for an account manager.
 */
export function SurveyDashboardPage({ apiFetch, onExport }: Props) {
  const [results, setResults] = useState<SurveyResults | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selectedPeriodId, setSelectedPeriodId] = useState<number | null>(null)
  const [showRespondents, setShowRespondents] = useState(false)

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

  const { summary, items, respondents, periods = [], period } = results

  return (
    <div className="sdb-root">
      <div className="sdb-period-bar">
        <label>
          Survey
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
        <div className="sdb-period-actions">
          <button className="secondary-action" type="button" onClick={() => load(selectedPeriodId)}>
            Refresh
          </button>
          <button className="primary-action" type="button" onClick={() => onExport(selectedPeriodId)}>
            Export Excel
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="sdb-summary-row">
        <StatCard label="Respondents" value={summary.unique_respondents} />
        <StatCard label="Submissions" value={summary.submissions} />
        <StatCard
          label="Items Answered"
          value={summary.answered_items}
          sub={`of ${summary.items} items`}
        />
        <StatCard label="Answers" value={summary.total_answers} sub="item × question rows" />
      </div>

      <div className="sdb-items-list">
        {items.length === 0 && <div className="empty-state">This survey has no items yet.</div>}
        {items.map((item, index) => (
          <article key={item.id} className={`sdb-result-card${item.response_count === 0 ? ' sdb-result-card--empty' : ''}`}>
            <header className="sdb-result-head">
              {isImageReference(item.image_url) ? (
                <img className="sdb-result-thumb" src={item.image_url} alt="" />
              ) : item.image_url ? (
                <img className="sdb-result-thumb" src={item.image_url} alt="" />
              ) : (
                <span className="sdb-result-thumb sdb-result-thumb--empty" aria-hidden="true">
                  {item.item_name.slice(0, 2).toUpperCase()}
                </span>
              )}
              <div className="sdb-result-copy">
                <span className="sdb-result-index">Item {index + 1}</span>
                <strong>{item.item_name}</strong>
                <div className="sdb-result-meta">
                  {item.cost && <span>{item.cost}</span>}
                  {item.item_number && <span>#{item.item_number}</span>}
                  {item.item_description && <span className="sdb-result-desc">{item.item_description}</span>}
                </div>
              </div>
              <span className="sdb-result-count">
                {item.response_count}
                <em>{item.response_count === 1 ? 'response' : 'responses'}</em>
              </span>
            </header>
            {item.response_count === 0 ? (
              <p className="muted sdb-result-none">No responses for this item yet.</p>
            ) : (
              <div className="sdb-tally-grid">
                {item.questions.map((tally) => (
                  <QuestionTallyBlock key={tally.question_id} tally={tally} />
                ))}
                {item.questions.length === 0 && (
                  <p className="muted">This item asked no questions.</p>
                )}
              </div>
            )}
          </article>
        ))}
      </div>

      <section className="sdb-respondents">
        <button
          className="secondary-action"
          type="button"
          onClick={() => setShowRespondents((current) => !current)}
          aria-expanded={showRespondents}
        >
          {showRespondents ? 'Hide respondents' : `Respondents (${respondents.length})`}
        </button>
        {showRespondents && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Submitted</th>
                  <th>Items answered</th>
                </tr>
              </thead>
              <tbody>
                {respondents.map((respondent) => (
                  <tr key={respondent.id}>
                    <td>{respondent.email || 'Anonymous'}</td>
                    <td>{new Date(respondent.created_at).toLocaleString()}</td>
                    <td>{respondent.items_answered}</td>
                  </tr>
                ))}
                {respondents.length === 0 && (
                  <tr>
                    <td colSpan={3} className="empty-state">
                      No submissions yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
