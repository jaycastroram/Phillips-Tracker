import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { SurveyQuestionEditor } from '../components/SurveyQuestionEditor'
import { DEFAULT_SURVEY_QUESTIONS } from '../constants'
import { SurveyLayoutEditor } from '../surveyLayout'
import type { SurveyFilterField, SurveyItem, SurveyPeriod, SurveyQuestion } from '../types'

type Props = {
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>
}

function formatStamp(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

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
  const [editorItems, setEditorItems] = useState<SurveyItem[]>([])
  const [editorFilters, setEditorFilters] = useState<SurveyFilterField[]>([])
  const [editorQuestions, setEditorQuestions] = useState<SurveyQuestion[]>(DEFAULT_SURVEY_QUESTIONS)
  const [editorLoading, setEditorLoading] = useState(false)
  const [editorSaving, setEditorSaving] = useState(false)
  const [editorMessage, setEditorMessage] = useState('')
  const [editorToken, setEditorToken] = useState('')
  const [linkMessage, setLinkMessage] = useState('')
  const [listCollapsed, setListCollapsed] = useState(false)
  const editorRef = useRef<HTMLDivElement | null>(null)

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

  // The create form shows the questions the new survey will actually get: the
  // company template for a fresh survey, or the source survey's own questions
  // when one is picked (the editor is read-only in that case).
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
          // A copied survey keeps its own questions: sending the template here
          // would override them, because explicit questions always win.
          ...(copyFromId === '' ? { questions: newQuestions } : {}),
        }),
      })
      if (!response.ok) {
        const data = await response.json().catch(() => null)
        throw new Error(data?.detail || 'Unable to create survey.')
      }
      const data = await response.json()
      setNewName('')
      setCopyFromId('')
      setOpenImmediately(false)
      setMessage(`Created “${data.period.name}”.`)
      await loadPeriods()
      setEditingPeriodId(data.period.id)
      await loadPeriodEditor(data.period.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to create survey.')
    } finally {
      setCreating(false)
    }
  }

  async function loadPeriodEditor(periodId: number) {
    setEditorLoading(true)
    setEditorMessage('')
    setError('')
    setListCollapsed(true)
    try {
      const response = await apiFetch(`/admin/survey-periods/${periodId}`)
      if (!response.ok) throw new Error('Unable to load survey layout.')
      const data = await response.json()
      setEditorItems(data.items ?? [])
      setEditorFilters(data.filter_fields ?? [])
      setEditorQuestions(
        Array.isArray(data.questions) && data.questions.length > 0
          ? data.questions
          : DEFAULT_SURVEY_QUESTIONS,
      )
      setEditorToken(String(data.period?.survey_token ?? ''))
      setEditingPeriodId(periodId)
      window.requestAnimationFrame(() => {
        editorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load survey layout.')
    } finally {
      setEditorLoading(false)
    }
  }

  async function savePeriodLayout(items: SurveyItem[], filterFields: SurveyFilterField[]) {
    if (editingPeriodId === null) return
    setEditorSaving(true)
    setEditorMessage('')
    setError('')
    try {
      const response = await apiFetch(`/admin/survey-periods/${editingPeriodId}/layout`, {
        method: 'PUT',
        body: JSON.stringify({
          filter_fields: filterFields,
          questions: editorQuestions,
          items: items.map((item) => ({
            catalog_item_id: item.id,
            in_survey: item.in_survey,
            response_width: item.response_width,
            page_break_before: item.page_break_before,
            image_display_size: item.image_display_size ?? 'medium',
            image_fit: item.image_fit ?? 'contain',
          })),
        }),
      })
      if (!response.ok) {
        const data = await response.json().catch(() => null)
        throw new Error(data?.detail || 'Unable to save survey layout.')
      }
      setEditorMessage('Survey layout saved.')
      await loadPeriodEditor(editingPeriodId)
      await loadPeriods()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to save survey layout.')
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
        setEditorItems([])
        setEditorQuestions(DEFAULT_SURVEY_QUESTIONS)
      }
      setMessage(`Deleted “${period.name}”.`)
      await loadPeriods()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to delete survey.')
    }
  }

  function surveyLink(token: string) {
    return `${window.location.origin}/survey?t=${encodeURIComponent(token)}`
  }

  async function copySurveyLink() {
    if (!editorToken) return
    setLinkMessage('')
    try {
      await navigator.clipboard.writeText(surveyLink(editorToken))
      setLinkMessage('Survey link copied.')
    } catch {
      setLinkMessage('This browser blocked the clipboard — select the link and copy it.')
    }
  }

  async function rotateSurveyToken() {
    const period = periods.find((item) => item.id === editingPeriodId)
    if (!period) return
    if (!window.confirm(
      `Replace the link for “${period.name}”? The current link stops working immediately.`,
    )) return
    setLinkMessage('')
    setError('')
    try {
      const response = await apiFetch(`/admin/survey-periods/${period.id}/rotate-token`, { method: 'POST' })
      if (!response.ok) throw new Error('Unable to replace the survey link.')
      const data = await response.json()
      setEditorToken(String(data.period?.survey_token ?? ''))
      setLinkMessage('New link issued. The old one no longer opens this survey.')
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

      <section className={`survey-periods-shell${listCollapsed ? ' is-collapsed' : ''}`}>
        <div className="table-tools survey-periods-toolbar">
          <div>
            <strong>{loading ? 'Loading surveys...' : `${periods.length} surveys`}</strong>
            {listCollapsed && editingPeriod && (
              <p className="survey-periods-collapsed-note">
                Editing “{editingPeriod.name}”. Expand the list to switch surveys.
              </p>
            )}
          </div>
          <div className="table-tool-actions">
            {editingPeriodId !== null && (
              <button
                aria-expanded={!listCollapsed}
                aria-label={listCollapsed ? 'Expand survey list' : 'Collapse survey list'}
                className="survey-periods-toggle"
                type="button"
                onClick={() => setListCollapsed((current) => !current)}
              >
                <span aria-hidden="true">{listCollapsed ? '▾' : '▴'}</span>
              </button>
            )}
            <button type="button" onClick={loadPeriods}>
              Refresh
            </button>
          </div>
        </div>

        {!listCollapsed && (
          <>
            <form className="survey-create-card" onSubmit={createSurvey}>
              <div className="survey-create-heading">
                <h2>Create survey</h2>
                <p className="survey-section-subtitle">
                  Name the survey, set the questions it asks, then save. Items start from the catalog
                  layout you can fine-tune afterwards.
                </p>
              </div>

              <section className="survey-create-section">
                <h3>Survey details</h3>
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
                    <span className="survey-field-label">Copy from</span>
                    <select
                      value={copyFromId}
                      onChange={(event) =>
                        setCopyFromId(event.target.value ? Number(event.target.value) : '')
                      }
                    >
                      <option value="">Current catalog layout</option>
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
                      : undefined
                  }
                  questions={newQuestions}
                  onChange={setNewQuestions}
                />
              </section>

              <div className="survey-create-actions">
                <button
                  className="secondary-button"
                  disabled={creating}
                  type="button"
                  onClick={resetCreateForm}
                >
                  Cancel
                </button>
                <button className="primary-action" disabled={creating} type="submit">
                  {creating ? 'Saving...' : 'Save survey'}
                </button>
              </div>
            </form>

            <div className="table-scroll survey-periods-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Status</th>
                    <th>Items</th>
                    <th>Opened</th>
                    <th>Closed</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {periods.map((period) => (
                    <tr key={period.id} className={editingPeriodId === period.id ? 'is-selected' : ''}>
                      <td>
                        <strong>{period.name}</strong>
                      </td>
                      <td>
                        <span className={`survey-period-status survey-period-status--${period.status}`}>
                          {period.status}
                        </span>
                      </td>
                      <td>{period.item_count ?? 0}</td>
                      <td>{formatStamp(period.opens_at)}</td>
                      <td>{formatStamp(period.closes_at)}</td>
                      <td className="actions survey-period-actions">
                        <button type="button" onClick={() => loadPeriodEditor(period.id)}>
                          Edit Layout
                        </button>
                        {period.status !== 'open' && (
                          <button type="button" onClick={() => setPeriodStatus(period, 'open')}>
                            Open
                          </button>
                        )}
                        {period.status === 'open' && (
                          <button type="button" onClick={() => setPeriodStatus(period, 'closed')}>
                            Close
                          </button>
                        )}
                        {period.status === 'draft' && (
                          <button type="button" onClick={() => deletePeriod(period)}>
                            Delete
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                  {!loading && periods.length === 0 && (
                    <tr>
                      <td colSpan={6} className="empty-state">
                        No surveys yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      {editingPeriod && (
        <div className="survey-periods-editor" ref={editorRef}>
          <p className="survey-link-label">Survey link (read-only)</p>
          <div className="survey-link-row">
            <input readOnly value={editorToken ? surveyLink(editorToken) : 'Loading...'} aria-label="Survey link" />
            <button type="button" onClick={copySurveyLink} disabled={!editorToken}>
              Copy link
            </button>
            <button type="button" onClick={rotateSurveyToken} disabled={!editorToken}>
              Replace link
            </button>
          </div>
          <p className="survey-link-hint">
            This link is the only way into the survey. Anyone with it can answer while the survey is open;
            replacing it kills the old link at once.
          </p>
          {linkMessage && <p className="survey-link-message">{linkMessage}</p>}
          {!editorLoading && (
            <SurveyQuestionEditor
              disabled={editingPeriod.status === 'closed' || editorSaving}
              questions={editorQuestions}
              onChange={setEditorQuestions}
            />
          )}
          <SurveyLayoutEditor
            title={`Edit “${editingPeriod.name}”`}
            description={
              editingPeriod.status === 'closed'
                ? 'This survey is closed. Re-open it as a draft/open survey before changing the layout.'
                : 'Choose catalog items for this survey period, set order, response width, and page breaks.'
            }
            showBuyBook={false}
            saveLabel="Save Survey Layout"
            disabled={editingPeriod.status === 'closed'}
            filterFields={editorFilters}
            items={editorItems}
            loading={editorLoading}
            message={editorMessage}
            saving={editorSaving}
            onSave={savePeriodLayout}
          />
        </div>
      )}
    </div>
  )
}
