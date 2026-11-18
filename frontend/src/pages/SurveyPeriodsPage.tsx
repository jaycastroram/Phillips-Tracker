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

  // The create form starts from the company template, so a new survey inherits
  // the same questions the last saved layout has (interest required by default).
  useEffect(() => {
    let cancelled = false
    async function loadTemplate() {
      try {
        const response = await apiFetch('/admin/survey-layout')
        if (!response.ok) return
        const data = await response.json()
        if (cancelled) return
        if (Array.isArray(data.questions) && data.questions.length > 0) {
          setNewQuestions(data.questions)
        }
      } catch {
        /* the default template stands in */
      }
    }
    loadTemplate()
    return () => {
      cancelled = true
    }
  }, [apiFetch])

  async function createPeriod(event: FormEvent<HTMLFormElement>) {
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
          questions: newQuestions,
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
            <form className="admin-create-card survey-create-inline" onSubmit={createPeriod}>
              <h2>Create Survey</h2>
              <p>Start a new survey period from the current catalog layout, or copy a past survey.</p>
              <label>
                Survey name
                <input
                  required
                  value={newName}
                  placeholder="Spring 2026 Pre-Buy"
                  onChange={(event) => setNewName(event.target.value)}
                />
              </label>
              <label>
                Copy from
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
              <label className="checkbox-label">
                <input
                  checked={openImmediately}
                  type="checkbox"
                  onChange={(event) => setOpenImmediately(event.target.checked)}
                />
                Open immediately (closes any currently open survey)
              </label>
              <SurveyQuestionEditor
                disabled={creating || copyFromId !== ''}
                questions={newQuestions}
                onChange={setNewQuestions}
              />
              {copyFromId !== '' && (
                <p className="survey-layout-filter-note">
                  This survey copies the questions from the selected past survey.
                </p>
              )}
              <button className="primary-action" disabled={creating} type="submit">
                {creating ? 'Creating...' : 'Create Survey'}
              </button>
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
