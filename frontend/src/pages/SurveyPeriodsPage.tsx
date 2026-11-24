import { useCallback, useEffect, useMemo, useState } from 'react'
import { SurveyEditorDialog, emptyItemDraft } from '../components/SurveyEditorDialog'
import { DEFAULT_SURVEY_QUESTIONS } from '../constants'
import type {
  SurveyItemDraft,
  SurveyItemPayload,
  SurveyOwnedItem,
  SurveyPeriod,
  SurveyQuestion,
} from '../types'

type Props = {
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>
  /** The company whose public link this page hands out ("phillips"). */
  companySlug: string
}

function formatStamp(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function ownedItemToDraft(item: SurveyOwnedItem, index: number): SurveyItemDraft {
  return {
    key: `saved-${item.id}-${index}`,
    id: item.id,
    item_name: item.item_name,
    cost: item.cost ?? '',
    image_url: item.image_url ?? '',
    item_number: item.item_number ?? '',
    item_description: item.item_description ?? '',
    question_ids: item.question_ids ?? [],
  }
}

/**
 * Surveys, as a list.
 *
 * Creating and editing happen in a dialog over this list, not in a form pinned
 * above it: a survey is a pool of questions plus a handful of items, and the
 * page underneath stays a list you can read at a glance.
 */
export function SurveyPeriodsPage({ apiFetch, companySlug }: Props) {
  const [periods, setPeriods] = useState<SurveyPeriod[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [dialogMode, setDialogMode] = useState<'create' | 'edit' | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [dialogInitial, setDialogInitial] = useState<{
    name: string
    questions: SurveyQuestion[]
    items: SurveyItemDraft[]
    openImmediately: boolean
  } | null>(null)
  const [dialogLoading, setDialogLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [dialogError, setDialogError] = useState('')
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

  const openSurvey = useMemo(() => periods.find((period) => period.status === 'open') ?? null, [periods])

  const staticLink = `${window.location.origin}/${companySlug}/survey`
  const tokenLink = (token: string) =>
    `${window.location.origin}/${companySlug}/survey?t=${encodeURIComponent(token)}`

  async function copyText(value: string, note: string) {
    setLinkMessage('')
    try {
      await navigator.clipboard.writeText(value)
      setLinkMessage(note)
    } catch {
      setLinkMessage('This browser blocked the clipboard — select the link and copy it.')
    }
  }

  async function replaceToken(period: SurveyPeriod) {
    if (!window.confirm(
      `Replace the private link for “${period.name}”? The current link stops working immediately.`,
    )) return
    setLinkMessage('')
    setError('')
    try {
      const response = await apiFetch(`/admin/survey-periods/${period.id}/rotate-token`, { method: 'POST' })
      if (!response.ok) throw new Error('Unable to replace the survey link.')
      setLinkMessage('New private link issued. The old one no longer opens this survey.')
      await loadPeriods()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to replace the survey link.')
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

  const uploadItemImage = useCallback(async (file: File) => {
    const body = new FormData()
    body.append('file', file)
    const res = await apiFetch('/uploads/survey-item', { method: 'POST', body })
    if (!res.ok) {
      const detail = await res.json().catch(() => null)
      throw new Error(detail?.detail || 'Image upload failed.')
    }
    const data = await res.json()
    return String(data.url || '')
  }, [apiFetch])

  function openCreateDialog() {
    setDialogMode('create')
    setEditingId(null)
    setDialogError('')
    // A new survey starts from the pool the last survey used — the questions
    // are defined once per survey and normally edited in place. The built-in
    // default is only the fallback for a company with no surveys at all.
    const latestPool = periods[0]?.questions ?? []
    const questions = latestPool.length > 0 ? latestPool : DEFAULT_SURVEY_QUESTIONS
    setDialogInitial({
      name: '',
      questions,
      items: [emptyItemDraft(questions.map((question) => question.id))],
      openImmediately: false,
    })
  }

  async function openEditDialog(period: SurveyPeriod) {
    setDialogMode('edit')
    setEditingId(period.id)
    setDialogError('')
    setDialogInitial(null)
    setDialogLoading(true)
    try {
      const response = await apiFetch(`/admin/survey-periods/${period.id}`)
      if (!response.ok) throw new Error('Unable to load this survey.')
      const data = await response.json()
      setDialogInitial({
        name: String(data.period?.name ?? period.name),
        questions:
          Array.isArray(data.questions) && data.questions.length > 0
            ? data.questions
            : DEFAULT_SURVEY_QUESTIONS,
        items: (data.items ?? []).map((item: SurveyOwnedItem, index: number) => ownedItemToDraft(item, index)),
        openImmediately: false,
      })
    } catch (err) {
      setDialogError(err instanceof Error ? err.message : 'Unable to load this survey.')
      setDialogMode(null)
    } finally {
      setDialogLoading(false)
    }
  }

  function closeDialog() {
    setDialogMode(null)
    setEditingId(null)
    setDialogInitial(null)
    setDialogError('')
    setSaving(false)
  }

  async function saveSurvey(payload: {
    name: string
    questions: SurveyQuestion[]
    items: SurveyItemPayload[]
    openImmediately: boolean
  }) {
    setSaving(true)
    setDialogError('')
    setMessage('')
    setError('')
    try {
      const body = JSON.stringify({
        name: payload.name,
        questions: payload.questions,
        items: payload.items,
      })
      const response =
        dialogMode === 'edit' && editingId !== null
          ? await apiFetch(`/admin/survey-periods/${editingId}/layout`, { method: 'PUT', body })
          : await apiFetch('/admin/survey-periods', {
              method: 'POST',
              body: JSON.stringify({
                name: payload.name,
                open_immediately: payload.openImmediately,
                questions: payload.questions,
                items: payload.items,
              }),
            })
      if (!response.ok) {
        const data = await response.json().catch(() => null)
        throw new Error(data?.detail || 'Unable to save this survey.')
      }
      setMessage(
        dialogMode === 'edit'
          ? `Saved “${payload.name}”.`
          : `Created “${payload.name}”.`,
      )
      closeDialog()
      await loadPeriods()
    } catch (err) {
      setDialogError(err instanceof Error ? err.message : 'Unable to save this survey.')
      setSaving(false)
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
      setMessage(`Deleted “${period.name}”.`)
      await loadPeriods()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to delete survey.')
    }
  }

  return (
    <div className="survey-periods-page">
      {error && <div className="error">{error}</div>}
      {message && <div className="success">{message}</div>}

      <section className="survey-link-card">
        <div className="survey-link-card-row">
          <div>
            <p className="survey-link-label">Public survey link</p>
            <input readOnly value={staticLink} aria-label="Public survey link" />
            <p className="survey-link-hint">
              Opens whichever survey is open for this company — the same link next time.
            </p>
          </div>
          <div className="survey-link-actions">
            <button type="button" onClick={() => copyText(staticLink, 'Public link copied.')}>
              Copy link
            </button>
            <button type="button" onClick={() => window.open(staticLink, '_blank', 'noopener')}>
              Open
            </button>
          </div>
        </div>
        {openSurvey && openSurvey.survey_token && (
          <div className="survey-link-card-row survey-link-card-private">
            <div>
              <p className="survey-link-label">Private link with a fixed token — {openSurvey.name}</p>
              <input readOnly value={tokenLink(openSurvey.survey_token)} aria-label="Private survey link" />
              <p className="survey-link-hint">
                Answers this exact survey while it is open. Replacing the token kills the old link at once.
              </p>
            </div>
            <div className="survey-link-actions">
              <button
                type="button"
                onClick={() => copyText(tokenLink(openSurvey.survey_token ?? ''), 'Private link copied.')}
              >
                Copy link
              </button>
              <button type="button" onClick={() => replaceToken(openSurvey)}>
                Replace token
              </button>
            </div>
          </div>
        )}
        {linkMessage && <p className="survey-link-message">{linkMessage}</p>}
      </section>

      <section className="survey-periods-shell">
        <div className="table-tools survey-periods-toolbar">
          <div>
            <strong>{loading ? 'Loading surveys...' : `${periods.length} surveys`}</strong>
          </div>
          <div className="table-tool-actions">
            <button type="button" onClick={loadPeriods}>
              Refresh
            </button>
            <button className="primary-action" type="button" onClick={openCreateDialog}>
              New survey
            </button>
          </div>
        </div>

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
                <tr key={period.id}>
                  <td>
                    <strong>{period.name}</strong>
                    <span className="survey-row-sub">
                      {period.item_count ?? 0} {(period.item_count ?? 0) === 1 ? 'item' : 'items'}
                    </span>
                  </td>
                  <td>
                    <span className={`survey-period-status survey-period-status--${period.status}`}>
                      {period.status}
                    </span>
                  </td>
                  <td>{period.response_count ?? 0}</td>
                  <td>{period.question_count ?? (period.questions?.length ?? 0)}</td>
                  <td>{formatStamp(period.opens_at)}</td>
                  <td className="actions survey-period-actions">
                    <button type="button" onClick={() => openEditDialog(period)}>
                      Edit
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
                    No surveys yet. Create one to add items and questions.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {dialogMode && dialogInitial && (
        <SurveyEditorDialog
          mode={dialogMode}
          initial={dialogInitial}
          saving={saving}
          error={dialogError}
          uploadImage={uploadItemImage}
          uploadQuestionImage={uploadQuestionImage}
          onCancel={closeDialog}
          onSave={saveSurvey}
        />
      )}
      {dialogMode && !dialogInitial && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal-card modal-card--lg">
            <p>{dialogLoading ? 'Loading survey…' : dialogError || 'Unable to open this survey.'}</p>
            <button className="secondary-button" type="button" onClick={closeDialog}>
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
