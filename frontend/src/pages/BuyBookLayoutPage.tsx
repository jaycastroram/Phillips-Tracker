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
  // Sync and Reset are admin routes. A viewer may open this page, so the
  // controls are hidden rather than shown to answer 403.
  isAdmin?: boolean
}

export function BuyBookLayoutPage({ apiFetch, companyId, isAdmin = false }: Props) {
  const [items, setItems] = useState<SurveyItem[]>([])
  const [periods, setPeriods] = useState<{ id: string; name: string; item_count?: number }[]>([])
  const [periodId, setPeriodId] = useState('')
  const [noPeriodData, setNoPeriodData] = useState(false)
  const [filterFields, setFilterFields] = useState<SurveyFilterField[]>([])
  const [layers, setLayers] = useState<BuyBookLayer[]>(DEFAULT_PAGE_BREAK_LAYERS)
  const [bookLink, setBookLink] = useState('')
  const [linkBusy, setLinkBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [resetNote, setResetNote] = useState('')

  async function resetItem(item: SurveyItem) {
    setResetNote('')
    setError('')
    try {
      const res = await apiFetch(`/admin/catalog/items/${item.id}/reset`, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || 'Unable to reset this item.')
      setResetNote(`${data.item?.item_name ?? item.item_name} follows NetSuite again.`)
      await loadLayout()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to reset this item.')
    }
  }

  const loadLayout = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const query = periodId ? `?period_id=${encodeURIComponent(periodId)}` : ''
      const res = await apiFetch(`/admin/buy-book-layout${query}`)
      if (!res.ok) throw new Error('Unable to load buy book layout.')
      const data = await res.json()
      setItems(data.items || [])
      setFilterFields(data.filter_fields || [])
      setLayers(normalizeLayers(data.page_break_layers, data.group_by))
      setPeriods(Array.isArray(data.periods) ? data.periods : [])
      setNoPeriodData(Boolean(data.no_period_data))
      if (data.period_id && data.period_id !== periodId) setPeriodId(String(data.period_id))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load buy book layout.')
    } finally {
      setLoading(false)
    }
  }, [apiFetch, periodId])

  useEffect(() => {
    loadLayout()
  }, [loadLayout])

  useEffect(() => {
    apiFetch('/admin/buy-book-link')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.path) setBookLink(data.path)
      })
      .catch(() => undefined)
  }, [apiFetch])

  async function replaceBookLink() {
    setLinkBusy(true)
    try {
      const res = await apiFetch('/admin/buy-book-link/rotate', { method: 'POST' })
      if (!res.ok) throw new Error('Unable to replace the link.')
      const data = await res.json()
      setBookLink(data.path)
      setMessage('New public book link created; the old one stopped working.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to replace the link.')
    } finally {
      setLinkBusy(false)
    }
  }

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

      <div className="buy-book-period-bar">
        <label>
          Buy-in period
          <select
            value={periodId}
            disabled={periods.length === 0}
            onChange={(event) => setPeriodId(event.target.value)}
          >
            {periods.length === 0 && <option value="">No buy-in periods yet</option>}
            {periods.map((period) => (
              <option key={period.id} value={period.id}>
                {period.name}{typeof period.item_count === 'number' ? ` (${period.item_count} items)` : ''}
              </option>
            ))}
          </select>
        </label>
        <p>
          {noPeriodData
            ? 'No buy-in periods have been imported yet. The book is empty until the periods export is in place.'
            : 'Only the items in this period are in the book. Sorting and page breaks are the layer stack below.'}
        </p>
      </div>

      <SurveyLayoutEditor
        title="Buy Book Layout"
        description="The layer stack decides ordering and page breaks for this period's items. Drag the image slider to preview print size."
        showSurvey={false}
        showBuyBook
        showResponseWidth={false}
        showImageControls
        filterLegend="Buy book options"
        filterExtra={(
          <>
            <PageBreakLayers layers={layers} onChange={setLayers} saving={saving} />
            {isAdmin && (
              <>
                <CatalogSyncPanel apiFetch={apiFetch} onDone={loadLayout} />
                <DownloadBookButton apiFetch={apiFetch} companyId={companyId} />
              </>
            )}
            <div className="survey-layout-filter-row">
              <p className="survey-layout-filter-label">Public book link</p>
              <p className="survey-layout-filter-note">
                Read-only with no sign-in: anyone holding this link can open the book, tick items
                and print. Replace it if it has travelled too far.
              </p>
              <div className="buy-book-link-row">
                <input readOnly value={bookLink} />
                <button type="button" onClick={() => navigator.clipboard?.writeText(window.location.origin + bookLink)}>
                  Copy
                </button>
                <button type="button" onClick={() => window.open(window.location.origin + bookLink, '_blank', 'noopener')}>
                  Open
                </button>
                <button type="button" disabled={linkBusy} onClick={replaceBookLink}>
                  Replace
                </button>
              </div>
            </div>
          </>
        )}
        includeLabel="In Buy Book"
        saveLabel="Save Buy Book Layout"
        filterFields={filterFields}
        items={items}
        loading={loading}
        message={message || resetNote}
        saving={saving}
        onSave={saveLayout}
        renderItemExtra={!isAdmin ? undefined : (item) =>
          item.netsuite_synced ? (
            <button
              className="secondary-button catalog-reset"
              type="button"
              title={
                item.needs_name
                  ? 'This item still has no NetSuite name to take.'
                  : 'Write the last-synced NetSuite values back onto this item.'
              }
              onClick={() => resetItem(item)}
            >
              Reset to NetSuite{item.needs_name ? ' (no name)' : ''}
            </button>
          ) : item.needs_name ? (
            <span className="catalog-needs-name">Needs a name</span>
          ) : null
        }
      />
      {!loading && items.length === 0 && (
        <p className="empty-state">
          {noPeriodData
            ? 'No buy-in periods have been imported yet, so there is nothing to lay out.'
            : 'This buy-in period has no items in the catalog yet.'}
        </p>
      )}
    </section>
  )
}

type SyncReport = {
  scanned: number
  created: number
  updated: number
  unchanged: number
  overridden: number
  retired: number
  needs_name: number
  label_misses: number
  excluded: number
  duration: number
  dry_run: boolean
}

type LastRun = {
  created_at: string
  user_email: string
  report: SyncReport
}

/** The catalog's NetSuite reconciliation: on demand, read-only, one company. */
function CatalogSyncPanel({ apiFetch, onDone }: { apiFetch: Props['apiFetch']; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [last, setLast] = useState<LastRun | null>(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')

  const loadLast = useCallback(async () => {
    try {
      const res = await apiFetch('/admin/catalog/sync')
      if (!res.ok) return
      const data = await res.json()
      setLast(data.last_run ?? null)
    } catch {
      /* the last-run line is decoration; the sync button still works */
    }
  }, [apiFetch])

  useEffect(() => {
    loadLast()
  }, [loadLast])

  async function run() {
    setBusy(true)
    setError('')
    setNote('')
    try {
      const res = await apiFetch('/admin/catalog/sync', { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail || 'The NetSuite sync failed.')
      const report: SyncReport = data.report
      setNote(
        `Synced: ${report.scanned} items scanned, ${report.created} created, ` +
          `${report.updated} updated, ${report.unchanged} unchanged, ` +
          `${report.overridden} overridden, ${report.retired} retired, ` +
          `${report.needs_name} without a name, ${report.label_misses} unlabelled facet values ` +
          `(${report.duration.toFixed(1)}s).`,
      )
      await loadLast()
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The NetSuite sync failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="buy-book-download catalog-sync">
      <p>
        Reconcile the catalog against the NetSuite item master: names, facets, description and
        sort order follow NetSuite, a value someone edited here stays edited. Read-only — nothing
        is written back to NetSuite.
      </p>
      <button type="button" className="secondary-action" onClick={run} disabled={busy}>
        {busy ? 'Syncing…' : 'Sync from NetSuite'}
      </button>
      {last ? (
        <p className="catalog-sync-last">
          Last run {new Date(last.created_at).toLocaleString()} by {last.user_email}:{' '}
          {last.report.created} created, {last.report.updated} updated, {last.report.retired} retired.
        </p>
      ) : (
        <p className="catalog-sync-last">Never run from NetSuite.</p>
      )}
      {note && <div className="success">{note}</div>}
      {error && <div className="error">{error}</div>}
    </div>
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
