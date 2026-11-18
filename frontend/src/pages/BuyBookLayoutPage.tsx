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
}

export function BuyBookLayoutPage({ apiFetch }: Props) {
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
            page_break_before: item.page_break_before,
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
        filterExtra={<PageBreakLayers layers={layers} onChange={setLayers} saving={saving} />}
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

/** Old layouts stored a single facet; it loads as one layer that breaks. */
function normalizeLayers(raw: unknown, legacyGroupBy: unknown): BuyBookLayer[] {
  if (Array.isArray(raw) && raw.length > 0) {
    const layers = raw
      .filter((entry): entry is { field: string; new_page?: boolean } => Boolean(entry) && typeof entry === 'object')
      .map((entry) => ({
        field: entry.field as BuyBookLayer['field'],
        new_page: entry.new_page !== false,
      }))
      .filter((layer) => BUY_BOOK_LAYER_FIELDS.some((option) => option.field === layer.field))
    if (layers.length > 0) return layers
  }
  const legacy = BUY_BOOK_LAYER_FIELDS.find((option) => option.field === legacyGroupBy)
  return legacy ? [{ field: legacy.field, new_page: true }] : DEFAULT_PAGE_BREAK_LAYERS
}

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

  return (
    <div className="survey-layout-filter-row">
      <p className="survey-layout-filter-label">Page breaks</p>
      <p className="survey-layout-filter-note">
        Items sort by these layers in order. Tick New page to start a page whenever that layer
        changes; unticked layers group and sort without breaking.
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
            <select
              aria-label={`Layer ${index + 1} facet`}
              disabled={saving}
              value={layer.field}
              onChange={(event) =>
                updateLayer(index, { field: event.target.value as BuyBookLayer['field'] })
              }
            >
              {BUY_BOOK_LAYER_FIELDS.map((option) => (
                <option key={option.field} value={option.field}>
                  {option.label}
                </option>
              ))}
            </select>
            <label className="buy-book-layer-break">
              <input
                checked={layer.new_page}
                disabled={saving}
                type="checkbox"
                onChange={(event) => updateLayer(index, { new_page: event.target.checked })}
              />
              New page
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
        disabled={saving}
        type="button"
        onClick={() => setLayers([...layers, { field: 'channel', new_page: false }])}
      >
        + Add layer
      </button>
    </div>
  )
}
