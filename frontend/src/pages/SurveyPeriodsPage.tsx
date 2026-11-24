import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { SurveyQuestionEditor } from '../components/SurveyQuestionEditor'
import { DEFAULT_COMPANY_SLUG, DEFAULT_SURVEY_QUESTIONS } from '../constants'
import type { SurveyPeriod, SurveyQuestion } from '../types'

type Props = {
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>
}

function formatStamp(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

/**
 * The survey builder: a name, an ordered question list, and a link.
 *
 * A survey is not a catalog anymore. There is no item picker and no layout of
 * product cards: a respondent opens the link, answers the questions once and
 * submits. This screen is therefore name + questions + open/close + link.
 */
export function SurveyPeriodsPage({ apiFetch }: Props) {
  const [periods, setPeriods] = useState<SurveyPeriod[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const [newName, setNewName] = useState('')
  const [copyFromId, setCopyFromId] = useState<number | ''>('')
  const [openImmediately, setOpenImmediately] = useState(false)
  const [newQuestions, setNewQuestions] = useState<SurveyQuestion[]>(DEFAULT_SURVEY_QUESTIONS)
  const [creating, setCreating] = useState(false)

  const [editingPeriodId, setEditingPeriodId] = useState<number | null>(null)
  const [editorQuestions, setEditorQuestions] = useState<SurveyQuestion[]>(DEFAULT_SURVEY_QUESTIONS)
  const [editorLoading, setEditorLoading] = useState(false)
  const [editorSaving, setEditorSaving] = useState(false)
  const [editorToken, setEditorToken] = useState('')
  const [linkMessage, setLinkMessage] = useState('')

  const loadPeriods = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await apiFetch('/admin/survey-periods')
      if (!response.ok) throw new Error('Unable to load surveys.')
      const data = await response.json()
      setPeriods(data.periods ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load surveys.')
    } finally {
      setLoading(false)
    }
  }, [apiFetch])

  useEffect(() => {
    loadPeriods()
  }, [loadPeriods])

  // The create form previews the questions a new survey gets: the company
  // template, or the picked survey's own question list.
  useEffect(() => {
    let cancelled = false
    async function loadQuestions() {
      const path = copyFromId === '' ? '/admin/survey-layout' : `/admin/survey-periods/${copyFromId}`
      try {
        const response = await apiFetch(path)
        if (!response.ok) return
        const data = await response.json()
        if (cancelled) return
        if (Array.isArray(data.questions) && data.questions.length > 0) {
          setNewQuestions(data.questions)
        }
      } catch {
        /* the backend still copies the source's questions; the list is a preview */
      }
    }
    loadQuestions()
    return () => {
      cancelled = true
    }
  }, [apiFetch, copyFromId])

  function resetCreateForm() {
    setNewName('')
    setCopyFromId('')
    setOpenImmediately(false)
    setNewQuestions(DEFAULT_SURVEY_QUESTIONS)
    setError('')
    setMessage('')
  }

  async function createSurvey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setCreating(true)
    setError('')
    setMessage('')
    try {
      const response = await apiFetch('/admin/survey-periods', {
        method: 'POST',
        body: JSON.stringify({
          name: newName.trim(),
          copy_from_period_id: copyFromId === '' ? null : copyFromId,
          open_immediately: openImmediately,
          ...(copyFromId === '' ? { questions: newQuestions } : {}),
        }),
      })
      if (!response.ok) {
        const data = await response.json().catch(() => null)
        throw new Error(data?.detail || 'Unable to create survey.')
      }
      const data = await response.json()
      resetCreateForm()
      setMessage(`Created “${data.period.name}”.`)
      await loadPeriods()
      await loadPeriodEditor(data.period.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to create survey.')
    } finally {
      setCreating(false)
    }
  }

  async function loadPeriodEditor(periodId: number) {
    setEditorLoading(true)
    setError('')
    try {
      const response = await apiFetch(`/admin/survey-periods/${periodId}`)
      if (!response.ok) throw new Error('Unable to load the survey.')
      const data = await response.json()
      setEditorQuestions(
        Array.isArray(data.questions) && data.questions.length > 0
          ? data.questions
          : DEFAULT_SURVEY_QUESTIONS,
      )
      setEditorToken(String(data.period?.survey_token ?? ''))
      setEditingPeriodId(periodId)
      setLinkMessage('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load the survey.')
    } finally {
      setEditorLoading(false)
    }
  }

  const uploadQuestionImage = useCallback(async (file: File) => {
    const body = new FormData()
    body.append('file', file)
    const res = await apiFetch('/uploads/survey-question', { method: 'POST', body })
    if (!res.ok) {
      const detail = await res.json().catch(() => null)
      throw new Error(detail?.detail || 'Image upload failed.')
    }
    const data = await res.json()
    return String(data.url || '')
  }, [apiFetch])

  async function saveQuestions() {
    if (editingPeriodId === null) return
    setEditorSaving(true)
    setError('')
    setMessage('')
    try {
      const response = await apiFetch(`/admin/survey-periods/${editingPeriodId}/layout`, {
        method: 'PUT',
        body: JSON.stringify({ filter_fields: [], questions: editorQuestions, items: [] }),
      })
      if (!response.ok) {
        const data = await response.json().catch(() => null)
        throw new Error(data?.detail || 'Unable to save the questions.')
      }
      setMessage('Survey saved.')
      await loadPeriods()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to save the questions.')
    } finally {
      setEditorSaving(false)
    }
  }

  async function setPeriodStatus(period: SurveyPeriod, status: SurveyPeriod['status']) {
    setError('')
    setMessage('')
    try {
      const response = await apiFetch(`/admin/survey-periods/${period.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ status }),
      })
      if (!response.ok) {
        const data = await response.json().catch(() => null)
        throw new Error(data?.detail || 'Unable to update survey status.')
      }
      setMessage(
        status === 'open'
          ? `“${period.name}” is now open.`
          : status === 'closed'
            ? `“${period.name}” is closed.`
            : `“${period.name}” moved to draft.`,
      )
      await loadPeriods()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to update survey status.')
    }
  }

  async function deletePeriod(period: SurveyPeriod) {
    if (!window.confirm(`Delete “${period.name}”? This cannot be undone.`)) return
    setError('')
    setMessage('')
    try {
      const response = await apiFetch(`/admin/survey-periods/${period.id}`, { method: 'DELETE' })
      if (!response.ok) {
        const data = await response.json().catch(() => null)
        throw new Error(data?.detail || 'Unable to delete survey.')
      }
      if (editingPeriodId === period.id) {
        setEditingPeriodId(null)
        setEditorQuestions(DEFAULT_SURVEY_QUESTIONS)
      }
      setMessage(`Deleted “${period.name}”.`)
      await loadPeriods()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to delete survey.')
    }
  }

  /** The static link a respondent opens — no token needed for Phillips. */
  function surveyLink() {
    return `${window.location.origin}/${DEFAULT_COMPANY_SLUG}/survey`
  }

  async function copySurveyLink() {
    setLinkMessage('')
    try {
      await navigator.clipboard.writeText(surveyLink())
      setLinkMessage('Survey link copied.')
    } catch {
      setLinkMessage('This browser blocked the clipboard — select the link and copy it.')
    }
  }

  async function rotateSurveyToken() {
    const period = periods.find((item) => item.id === editingPeriodId)
    if (!period) return
    if (!window.confirm(
      `Replace the private link for “${period.name}”? A link carrying the old token stops working immediately.`,
    )) return
    setLinkMessage('')
    setError('')
    try {
      const response = await apiFetch(`/admin/survey-periods/${period.id}/rotate-token`, { method: 'POST' })
      if (!response.ok) throw new Error('Unable to replace the survey link.')
      const data = await response.json()
      setEditorToken(String(data.period?.survey_token ?? ''))
      setLinkMessage('New private link issued. The old one no longer opens this survey.')
      await loadPeriods()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to replace the survey link.')
    }
  }

  const editingPeriod = periods.find((period) => period.id === editingPeriodId) ?? null

  return (
    <div className="survey-periods-page">
      {error && <div className="error">{error}</div>}
      {message && <div className="success">{message}</div>}

      <section className="survey-periods-shell">
        <div className="table-tools survey-periods-toolbar">
          <div>
            <strong>{loading ? 'Loading surveys...' : `${periods.length} surveys`}</strong>
          </div>
          <div className="table-tool-actions">
            <button type="button" onClick={loadPeriods}>Refresh</button>
          </div>
        </div>

        <form className="survey-create-card" onSubmit={createSurvey}>
          <div className="survey-create-heading">
            <h2>New survey</h2>
            <p className="survey-section-subtitle">
              Name it, write the questions, then share the link. Respondents answer the list once.
            </p>
          </div>

          <section className="survey-create-section">
            <div className="survey-create-details">
              <label>
                <span className="survey-field-label">Survey name</span>
                <input
                  required
                  value={newName}
                  placeholder="Spring 2026 Pre-Buy"
                  onChange={(event) => setNewName(event.target.value)}
                />
              </label>
              <label>
                <span className="survey-field-label">Copy questions from</span>
                <select
                  value={copyFromId}
                  onChange={(event) =>
                    setCopyFromId(event.target.value ? Number(event.target.value) : '')
                  }
                >
                  <option value="">Start with the default questions</option>
                  {periods.map((period) => (
                    <option key={period.id} value={period.id}>
                      {period.name} ({period.status})
                    </option>
                  ))}
                </select>
              </label>
              <label className="survey-create-open">
                <input
                  checked={openImmediately}
                  type="checkbox"
                  onChange={(event) => setOpenImmediately(event.target.checked)}
                />
                <span>
                  Open immediately
                  <span className="survey-create-hint">Closes any currently open survey</span>
                </span>
              </label>
            </div>
          </section>

          <section className="survey-create-section">
            <SurveyQuestionEditor
              disabled={creating || copyFromId !== ''}
              note={
                copyFromId !== ''
                  ? 'This survey copies the questions from the selected past survey.'
                  : 'These are the questions a respondent answers, in this order.'
              }
              questions={newQuestions}
              uploadImage={uploadQuestionImage}
              onChange={setNewQuestions}
            />
          </section>

          <div className="survey-create-actions">
            <button className="secondary-button" disabled={creating} type="button" onClick={resetCreateForm}>
              Cancel
            </button>
            <button className="primary-action" disabled={creating} type="submit">
              {creating ? 'Saving...' : 'Create survey'}
            </button>
          </div>
        </form>

        <div className="table-scroll survey-periods-scroll">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th>Responses</th>
                <th>Questions</th>
                <th>Opened</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {periods.map((period) => (
                <tr key={period.id} className={editingPeriodId === period.id ? 'is-selected' : ''}>
                  <td><strong>{period.name}</strong></td>
                  <td>
                    <span className={`survey-period-status survey-period-status--${period.status}`}>
                      {period.status}
                    </span>
                  </td>
                  <td>{period.item_count ?? 0}</td>
                  <td>{period.questions?.length ?? '—'}</td>
                  <td>{formatStamp(period.opens_at)}</td>
                  <td className="actions survey-period-actions">
                    <button type="button" onClick={() => loadPeriodEditor(period.id)}>
                      Edit
                    </button>
                    {period.status !== 'open' && (
                      <button type="button" onClick={() => setPeriodStatus(period, 'open')}>Open</button>
                    )}
                    {period.status === 'open' && (
                      <button type="button" onClick={() => setPeriodStatus(period, 'closed')}>Close</button>
                    )}
                    {period.status === 'draft' && (
                      <button type="button" onClick={() => deletePeriod(period)}>Delete</button>
                    )}
                  </td>
                </tr>
              ))}
              {!loading && periods.length === 0 && (
                <tr>
                  <td colSpan={6} className="empty-state">No surveys yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {editingPeriod && (
        <section className="survey-periods-editor">
          <h2>Edit “{editingPeriod.name}”</h2>

          <p className="survey-link-label">Survey link (share this)</p>
          <div className="survey-link-row">
            <input readOnly value={surveyLink()} aria-label="Survey link" />
            <button type="button" onClick={copySurveyLink}>Copy link</button>
            <button
              type="button"
              onClick={() => window.open(surveyLink(), '_blank', 'noopener')}
            >
              Open
            </button>
          </div>
          <p className="survey-link-hint">
            Anyone with this link can answer while the survey is open. It resolves the survey that
            is open for Phillips, so you can use the same link again next time.
          </p>
          {editorToken && (
            <p className="survey-link-hint">
              Private link with a fixed token:{' '}
              <code>{`${window.location.origin}/phillips/survey?t=${editorToken}`}</code>{' '}
              <button type="button" onClick={rotateSurveyToken}>Replace token</button>
            </p>
          )}
          {linkMessage && <p className="survey-link-message">{linkMessage}</p>}

          {editorLoading ? (
            <p className="empty-state">Loading the survey…</p>
          ) : (
            <>
              <SurveyQuestionEditor
                disabled={editingPeriod.status === 'closed' || editorSaving}
                note={
                  editingPeriod.status === 'closed'
                    ? 'This survey is closed. Re-open it before changing the questions.'
                    : 'Respondents answer these in this order.'
                }
                questions={editorQuestions}
                uploadImage={uploadQuestionImage}
                onChange={setEditorQuestions}
              />
              <div className="survey-create-actions">
                <button
                  className="primary-action"
                  type="button"
                  disabled={editingPeriod.status === 'closed' || editorSaving}
                  onClick={saveQuestions}
                >
                  {editorSaving ? 'Saving...' : 'Save questions'}
                </button>
              </div>
            </>
          )}
        </section>
      )}
    </div>
  )
}
