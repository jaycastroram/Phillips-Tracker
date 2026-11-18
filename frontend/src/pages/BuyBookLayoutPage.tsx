import { useCallback, useEffect, useState } from 'react'
import {
  BUY_BOOK_LAYER_FIELDS,
  DEFAULT_PAGE_BREAK_LAYERS,
  type BuyBookLayer,
} from '../constants'
import { SurveyLayoutEditor } from '../surveyLayout'
import type { SurveyFilterField, SurveyItem } from '../types'

type Props = {
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>
  // The company the page is showing. The PDF has to be the same one, so it
  // travels in the body (the route takes no query parameters).
  companyId?: number | null
}

export function BuyBookLayoutPage({ apiFetch, companyId }: Props) {
  const [items, setItems] = useState<SurveyItem[]>([])
  const [filterFields, setFilterFields] = useState<SurveyFilterField[]>([])
  const [layers, setLayers] = useState<BuyBookLayer[]>(DEFAULT_PAGE_BREAK_LAYERS)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const loadLayout = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await apiFetch('/admin/buy-book-layout')
      if (!res.ok) throw new Error('Unable to load buy book layout.')
      const data = await res.json()
      setItems(data.items || [])
      setFilterFields(data.filter_fields || [])
      setLayers(normalizeLayers(data.page_break_layers, data.group_by))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load buy book layout.')
    } finally {
      setLoading(false)
    }
  }, [apiFetch])

  useEffect(() => {
    loadLayout()
  }, [loadLayout])

  async function saveLayout(draftItems: SurveyItem[], draftFilters: SurveyFilterField[]) {
    setSaving(true)
    setMessage('')
    setError('')
    try {
      const res = await apiFetch('/admin/buy-book-layout', {
        method: 'PUT',
        body: JSON.stringify({
          filter_fields: draftFilters,
          page_break_layers: layers,
          items: draftItems.map((item) => ({
            id: item.id,
            in_buy_book: item.in_buy_book,
            image_display_size: item.image_display_size ?? 'medium',
            image_fit: item.image_fit ?? 'contain',
          })),
        }),
      })
      if (!res.ok) throw new Error('Unable to save buy book layout.')
      const data = await res.json()
      setItems(data.items || [])
      setFilterFields(data.filter_fields || [])
      setLayers(normalizeLayers(data.page_break_layers, data.group_by))
      setMessage('Buy book layout saved.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to save buy book layout.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="buy-book-layout-page">
      {error && <div className="error">{error}</div>}

      <SurveyLayoutEditor
        title="Buy Book Layout"
        description="Choose catalog items, the layer stack that decides sorting and page breaks, and the public filters. Drag the image slider to preview print size."
        showSurvey={false}
        showBuyBook
        showResponseWidth={false}
        showImageControls
        filterLegend="Buy book options"
        filterExtra={(
          <>
            <PageBreakLayers layers={layers} onChange={setLayers} saving={saving} />
            <DownloadBookButton apiFetch={apiFetch} companyId={companyId} />
          </>
        )}
        includeLabel="In Buy Book"
        saveLabel="Save Buy Book Layout"
        filterFields={filterFields}
        items={items}
        loading={loading}
        message={message}
        saving={saving}
        onSave={saveLayout}
      />
    </section>
  )
}

/** The book itself: the same cover, order and breaks the screen draws. */
function DownloadBookButton({ apiFetch, companyId }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function download() {
    setBusy(true)
    setError('')
    try {
      const body = { ...(companyId == null ? {} : { company_id: companyId }), tz_offset_minutes: -new Date().getTimezoneOffset() }
      const res = await apiFetch('/admin/buy-book/pdf', { method: 'POST', body: JSON.stringify(body) })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error(detail.detail || 'Unable to build the Buy Book PDF.')
      }
      const blob = await res.blob()
      const disposition = res.headers.get('Content-Disposition') || ''
      const filename = /filename="([^"]+)"/.exec(disposition)?.[1] || 'buy-book.pdf'
      const link = document.createElement('a')
      link.href = URL.createObjectURL(blob)
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(link.href)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to build the Buy Book PDF.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="buy-book-download">
      <p>Download the book as a PDF: the cover, then six items to a page.</p>
      <button type="button" className="secondary-action" onClick={download} disabled={busy}>
        {busy ? 'Building PDF…' : 'Download PDF'}
      </button>
      {error && <div className="error">{error}</div>}
    </div>
  )
}

/** Old layouts stored a single facet; it loads as one layer that breaks.
 *  An empty stack is a saved choice ("no grouping"), so it stays empty. */
function normalizeLayers(raw: unknown, legacyGroupBy: unknown): BuyBookLayer[] {
  if (Array.isArray(raw)) {
    if (raw.length === 0 && legacyGroupBy === 'none') return []
    const layers = raw
      .filter((entry): entry is { field: string; new_page?: boolean } => Boolean(entry) && typeof entry === 'object')
      .map((entry) => ({
        field: entry.field as BuyBookLayer['field'],
        new_page: entry.new_page !== false,
      }))
      .filter((layer) => BUY_BOOK_LAYER_FIELDS.some((option) => option.field === layer.field))
    if (layers.length > 0) return layers
    if (raw.length > 0) return DEFAULT_PAGE_BREAK_LAYERS
  }
  const legacy = BUY_BOOK_LAYER_FIELDS.find((option) => option.field === legacyGroupBy)
  return legacy ? [{ field: legacy.field, new_page: true }] : DEFAULT_PAGE_BREAK_LAYERS
}

const LAYER_LIMIT = 4

function PageBreakLayers({
  layers,
  onChange,
  saving,
}: {
  layers: BuyBookLayer[]
  onChange: (layers: BuyBookLayer[]) => void
  saving: boolean
}) {
  function setLayers(next: BuyBookLayer[]) {
    onChange(next)
  }

  function updateLayer(index: number, updates: Partial<BuyBookLayer>) {
    setLayers(layers.map((layer, i) => (i === index ? { ...layer, ...updates } : layer)))
  }

  function move(index: number, direction: -1 | 1) {
    const target = index + direction
    if (target < 0 || target >= layers.length) return
    const next = [...layers]
    const [moved] = next.splice(index, 1)
    next.splice(target, 0, moved)
    setLayers(next)
  }

  const ownerByField = new Map<string, number>()
  layers.forEach((layer, index) => {
    if (!ownerByField.has(layer.field)) ownerByField.set(layer.field, index)
  })
  const freeFields = BUY_BOOK_LAYER_FIELDS.filter((option) => !ownerByField.has(option.field))
  const canAdd = !saving && freeFields.length > 0 && layers.length < 4
  const labelOf = (field: string) => BUY_BOOK_LAYER_FIELDS.find((option) => option.field === field)?.label ?? field
  const sortedLabels = layers.map((layer) => labelOf(layer.field))
  const breakLabels = layers.filter((layer) => layer.new_page).map((layer) => labelOf(layer.field))
  const summary = layers.length === 0
    ? 'No grouping: items print in catalog order, with no page breaks.'
    : `Items ordered ${sortedLabels.join(' → ')} → item order; ${breakLabels.length > 0 ? `pages start when ${breakLabels.join(' or ')} changes.` : 'no page starts automatically.'}`

  return (
    <div className="survey-layout-filter-row">
      <p className="survey-layout-filter-label">Sorting and page breaks</p>
      <p className="survey-layout-filter-note">
        Sort by the first layer, then the next. Tick New page to start a page whenever that layer
        changes; an unticked layer still sorts.
      </p>
      <ol className="buy-book-layers">
        {layers.map((layer, index) => (
          <li key={`${layer.field}-${index}`} className="buy-book-layer-row">
            <span className="buy-book-layer-index" aria-hidden="true">
              {index + 1}
            </span>
            <div className="buy-book-layer-moves">
              <button
                aria-label={`Move layer ${index + 1} up`}
                disabled={saving || index === 0}
                type="button"
                onClick={() => move(index, -1)}
              >
                ↑
              </button>
              <button
                aria-label={`Move layer ${index + 1} down`}
                disabled={saving || index === layers.length - 1}
                type="button"
                onClick={() => move(index, 1)}
              >
                ↓
              </button>
            </div>
            <span className="buy-book-layer-label">Sort by</span>
            <select
              aria-label={`Layer ${index + 1} facet`}
              disabled={saving}
              value={layer.field}
              onChange={(event) =>
                updateLayer(index, { field: event.target.value as BuyBookLayer['field'] })
              }
            >
              {BUY_BOOK_LAYER_FIELDS.map((option) => {
                const usedAt = ownerByField.get(option.field)
                const usedAbove = usedAt !== undefined && usedAt !== index
                return (
                  <option disabled={usedAbove} key={option.field} value={option.field}>
                    {usedAbove ? `${option.label} (already used in layer ${(usedAt ?? 0) + 1})` : option.label}
                  </option>
                )
              })}
            </select>
            <label className="buy-book-layer-break">
              <input
                checked={layer.new_page}
                disabled={saving}
                type="checkbox"
                onChange={(event) => updateLayer(index, { new_page: event.target.checked })}
              />
              Start a new page when this changes
            </label>
            <button
              aria-label={`Remove layer ${index + 1}`}
              className="buy-book-layer-remove"
              disabled={saving || layers.length <= 1}
              type="button"
              onClick={() => setLayers(layers.filter((_, i) => i !== index))}
            >
              ✕
            </button>
          </li>
        ))}
      </ol>
      <button
        className="secondary-button buy-book-layer-add"
        disabled={!canAdd}
        type="button"
        onClick={() => freeFields[0] && setLayers([...layers, { field: freeFields[0].field, new_page: false }])}
      >
        + Add layer
      </button>
      {!canAdd && layers.length < LAYER_LIMIT ? (
        <p className="survey-layout-filter-note">Every facet is already a layer.</p>
      ) : null}
      {layers.length >= LAYER_LIMIT ? (
        <p className="survey-layout-filter-note">A book has at most four layers.</p>
      ) : null}
      <p className="buy-book-layer-summary">{summary}</p>
    </div>
  )
}
