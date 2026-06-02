import { useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import phillipsLogo from './assets/3184_Phillips Store Logo.png'
import smartBuyLogo from './assets/SmartBuy Logo Transparent.png'
import type {
  KanbanColumn,
  SurveyInterest,
  SurveyItem,
  SurveyResponseDraft,
  TrackerItem,
} from './types'
import {
  getViewerStatus,
  isImageReference,
  publicSheetLabel,
  viewerStatusLabel,
} from './utils'

type PublicKanbanColumn = {
  column: KanbanColumn
  items: TrackerItem[]
}

type BuyBookPageProps = {
  surveyItems: SurveyItem[]
  surveyLoading: boolean
  surveyError: string
  surveyQuery: string
  selectedSurveyItemIds: number[]
  setSurveyQuery: Dispatch<SetStateAction<string>>
  toggleSurveyItem: (itemId: number) => void
  printSurveyBuyBook: () => void
}

type SurveyPageProps = {
  surveyItems: SurveyItem[]
  surveyLoading: boolean
  surveyError: string
  surveyMessage: string
  surveyQuery: string
  selectedSurveyItemIds: number[]
  isSubmittingSurvey: boolean
  setSurveyQuery: Dispatch<SetStateAction<string>>
  toggleSurveyItem: (itemId: number) => void
  submitSurveyResponse: () => void
  bulkUpdateSurveyInterest: (interest: Exclude<SurveyInterest, ''>) => void
  getSurveyResponse: (itemId: number) => SurveyResponseDraft
  updateSurveyResponse: (itemId: number, updates: Partial<SurveyResponseDraft>) => void
}

type ViewerPageProps = {
  isPublicKanbanPath: boolean
  publicQuery: string
  publicStatusFilter: string
  publicStatusOptions: string[]
  publicLoading: boolean
  publicError: string
  publicItems: TrackerItem[]
  publicGridItems: TrackerItem[]
  publicKanbanColumns: PublicKanbanColumn[]
  setPublicQuery: Dispatch<SetStateAction<string>>
  setPublicStatusFilter: Dispatch<SetStateAction<string>>
  goToPath: (path: string) => void
}

function PublicCard({ item }: { item: TrackerItem }) {
  return (
    <article key={item.id} className={`viewer-card ${getViewerStatus(item)}`}>
      <div className="viewer-card-topline">
        <span>{publicSheetLabel(item.sheet)}</span>
        <strong>{viewerStatusLabel(getViewerStatus(item))}</strong>
      </div>
      {isImageReference(item.visual_reference) && (
        <a className="viewer-card-image" href={item.visual_reference} target="_blank" rel="noreferrer">
          <img src={item.visual_reference} alt="" />
        </a>
      )}
      <h3>{item.item_name || 'Untitled Item'}</h3>
      <p className="viewer-card-meta">
        {[item.brand, item.program_name].filter(Boolean).join(' | ') || 'No brand/program listed'}
      </p>
      <dl>
        <div>
          <dt>Qty</dt>
          <dd>{item.qty || '-'}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{item.current_status || 'No Status'}</dd>
        </div>
      </dl>
      {item.important_notes && <p className="viewer-notes">{item.important_notes}</p>}
    </article>
  )
}

function getSurveyProductType(item: SurveyItem) {
  const label = `${item.item_name} ${item.item_description}`.toLowerCase()
  if (label.includes('pennant')) return 'Pennants'
  if (label.includes('frame')) return 'A-Frames'
  if (label.includes('card')) return 'Display Cards'
  if (label.includes('tacker') || label.includes('sign')) return 'Signage'
  if (label.includes('display')) return 'Displays'
  return 'Other'
}

function uniqueOptions(values: string[]) {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))].sort((first, second) =>
    first.localeCompare(second),
  )
}

function useSurveyFilters(items: SurveyItem[]) {
  const [brandFilter, setBrandFilter] = useState('')
  const [channelFilter, setChannelFilter] = useState('')
  const [productTypeFilter, setProductTypeFilter] = useState('')

  const brandOptions = useMemo(() => uniqueOptions(items.map((item) => item.brand)), [items])
  const channelOptions = useMemo(() => uniqueOptions(items.map((item) => item.channel)), [items])
  const productTypeOptions = useMemo(
    () => uniqueOptions(items.map((item) => getSurveyProductType(item))),
    [items],
  )
  const filteredItems = useMemo(() => {
    return items.filter((item) => {
      if (brandFilter && item.brand !== brandFilter) return false
      if (channelFilter && item.channel !== channelFilter) return false
      if (productTypeFilter && getSurveyProductType(item) !== productTypeFilter) return false
      return true
    })
  }, [brandFilter, channelFilter, items, productTypeFilter])

  return {
    brandFilter,
    brandOptions,
    channelFilter,
    channelOptions,
    filteredItems,
    productTypeFilter,
    productTypeOptions,
    resetFilters: () => {
      setBrandFilter('')
      setChannelFilter('')
      setProductTypeFilter('')
    },
    setBrandFilter,
    setChannelFilter,
    setProductTypeFilter,
  }
}

function SurveyFilterControls({
  brandFilter,
  brandOptions,
  channelFilter,
  channelOptions,
  productTypeFilter,
  productTypeOptions,
  setBrandFilter,
  setChannelFilter,
  setProductTypeFilter,
}: ReturnType<typeof useSurveyFilters>) {
  return (
    <div className="survey-filter-grid">
      <label>
        Brand
        <select value={brandFilter} onChange={(event) => setBrandFilter(event.target.value)}>
          <option value="">All brands</option>
          {brandOptions.map((brand) => (
            <option key={brand} value={brand}>
              {brand}
            </option>
          ))}
        </select>
      </label>
      <label>
        Channel
        <select value={channelFilter} onChange={(event) => setChannelFilter(event.target.value)}>
          <option value="">All channels</option>
          {channelOptions.map((channel) => (
            <option key={channel} value={channel}>
              {channel}
            </option>
          ))}
        </select>
      </label>
      <label>
        Product Type
        <select value={productTypeFilter} onChange={(event) => setProductTypeFilter(event.target.value)}>
          <option value="">All product types</option>
          {productTypeOptions.map((productType) => (
            <option key={productType} value={productType}>
              {productType}
            </option>
          ))}
        </select>
      </label>
    </div>
  )
}

export function BuyBookPage({
  surveyItems,
  surveyLoading,
  surveyError,
  surveyQuery,
  selectedSurveyItemIds,
  setSurveyQuery,
  toggleSurveyItem,
  printSurveyBuyBook,
}: BuyBookPageProps) {
  const surveyFilters = useSurveyFilters(surveyItems)
  const allVisibleSelected =
    surveyFilters.filteredItems.length > 0 &&
    surveyFilters.filteredItems.every((item) => selectedSurveyItemIds.includes(item.id))

  function toggleVisibleSurveyItems() {
    surveyFilters.filteredItems.forEach((item) => {
      const isSelected = selectedSurveyItemIds.includes(item.id)
      if (allVisibleSelected ? isSelected : !isSelected) toggleSurveyItem(item.id)
    })
  }

  return (
    <main className="app-shell viewer-shell survey-shell">
      <header className="brand-header">
        <div className="brand-topline">
          <img className="smartbuy-logo" src={smartBuyLogo} alt="SmartBuy" />
          <span>Buy Book</span>
        </div>
      </header>

      <section className="client-title-row viewer-title-row">
        <div className="client-title-copy">
          <div className="client-title-divider" />
          <div>
            <p className="eyebrow">SmartBuy Buy Book</p>
            <h1>Buy Book</h1>
            <p className="subtitle">Select items and print a clean buy book for team review.</p>
          </div>
        </div>
        <img className="phillips-logo" src={phillipsLogo} alt="Phillips Distilling Co" />
      </section>

      <section className="viewer-toolbar">
        <label>
          Search
          <input
            value={surveyQuery}
            onChange={(event) => setSurveyQuery(event.target.value)}
            placeholder="Item, brand, channel, description..."
          />
        </label>
        <div className="viewer-toolbar-actions">
          <button className="secondary-action" type="button" onClick={() => setSurveyQuery('')}>
            Reset Search
          </button>
          <SurveyFilterControls {...surveyFilters} />
          <button
            className="secondary-action"
            type="button"
            onClick={toggleVisibleSurveyItems}
            disabled={surveyFilters.filteredItems.length === 0}
          >
            {allVisibleSelected ? 'Clear Selection' : 'Select All'}
          </button>
          <button
            className="primary-action"
            type="button"
            onClick={printSurveyBuyBook}
            disabled={selectedSurveyItemIds.length === 0}
          >
            Print Buy Book ({selectedSurveyItemIds.length})
          </button>
          <span>{surveyLoading ? 'Loading...' : `${surveyFilters.filteredItems.length.toLocaleString()} items`}</span>
        </div>
      </section>

      {surveyError && <div className="error">{surveyError}</div>}

      <section className="survey-grid" aria-label="Buy book items">
        {surveyFilters.filteredItems.map((item) => (
          <article key={item.id} className="survey-card buy-book-card">
            <label className="survey-select">
              <input
                checked={selectedSurveyItemIds.includes(item.id)}
                type="checkbox"
                onChange={() => toggleSurveyItem(item.id)}
              />
              <span>Select</span>
            </label>
            <div className="survey-image">
              {isImageReference(item.image_url) ? (
                <img src={item.image_url} alt="" />
              ) : (
                <span>{item.brand.slice(0, 2).toUpperCase() || 'SB'}</span>
              )}
            </div>
            <div className="survey-card-copy">
              <h2>{item.item_name}</h2>
              <p>{item.item_description || 'No description provided yet.'}</p>
            </div>
          </article>
        ))}
        {!surveyLoading && surveyFilters.filteredItems.length === 0 && (
          <div className="empty-state">No buy book items found.</div>
        )}
      </section>
    </main>
  )
}

export function SurveyPage({
  surveyItems,
  surveyLoading,
  surveyError,
  surveyMessage,
  surveyQuery,
  selectedSurveyItemIds,
  isSubmittingSurvey,
  setSurveyQuery,
  toggleSurveyItem,
  submitSurveyResponse,
  bulkUpdateSurveyInterest,
  getSurveyResponse,
  updateSurveyResponse,
}: SurveyPageProps) {
  const surveyFilters = useSurveyFilters(surveyItems)
  const allVisibleSelected =
    surveyFilters.filteredItems.length > 0 &&
    surveyFilters.filteredItems.every((item) => selectedSurveyItemIds.includes(item.id))

  function toggleVisibleSurveyItems() {
    surveyFilters.filteredItems.forEach((item) => {
      const isSelected = selectedSurveyItemIds.includes(item.id)
      if (allVisibleSelected ? isSelected : !isSelected) toggleSurveyItem(item.id)
    })
  }

  return (
    <main className="app-shell viewer-shell survey-shell">
      <header className="brand-header">
        <div className="brand-topline">
          <img className="smartbuy-logo" src={smartBuyLogo} alt="SmartBuy" />
          <span>Public Survey</span>
        </div>
      </header>

      <section className="client-title-row viewer-title-row">
        <div className="client-title-copy">
          <div className="client-title-divider" />
          <div>
            <p className="eyebrow">Pre-Buy Survey</p>
            <h1>Product Interest Survey</h1>
            <p className="subtitle">
              Make your voice count and review Buy In item candidates. This is your opportunity to influence what gets purchased and stocked.
            </p>
          </div>
        </div>
        <img className="phillips-logo" src={phillipsLogo} alt="Phillips Distilling Co" />
      </section>

      <section className="viewer-toolbar">
        <label>
          Search
          <input
            value={surveyQuery}
            onChange={(event) => setSurveyQuery(event.target.value)}
            placeholder="Item, brand, channel, description..."
          />
        </label>
        <div className="viewer-toolbar-actions">
          <button className="secondary-action" type="button" onClick={() => setSurveyQuery('')}>
            Reset Search
          </button>
          <SurveyFilterControls {...surveyFilters} />
          <button
            className="secondary-action"
            type="button"
            onClick={toggleVisibleSurveyItems}
            disabled={surveyFilters.filteredItems.length === 0}
          >
            {allVisibleSelected ? 'Clear Selection' : 'Select All'}
          </button>
          <button
            className="primary-action"
            type="button"
            onClick={submitSurveyResponse}
            disabled={selectedSurveyItemIds.length === 0}
          >
            {isSubmittingSurvey ? 'Submitting...' : `Submit Selected (${selectedSurveyItemIds.length})`}
          </button>
          <button
            className="secondary-action"
            type="button"
            onClick={() => bulkUpdateSurveyInterest('Interested')}
            disabled={selectedSurveyItemIds.length === 0}
          >
            Mark Interested
          </button>
          <button
            className="secondary-action"
            type="button"
            onClick={() => bulkUpdateSurveyInterest('Not Interested')}
            disabled={selectedSurveyItemIds.length === 0}
          >
            Mark Not Interested
          </button>
          <span>{surveyLoading ? 'Loading...' : `${surveyFilters.filteredItems.length.toLocaleString()} items`}</span>
        </div>
      </section>

      {surveyError && <div className="error">{surveyError}</div>}
      {surveyMessage && <div className="success">{surveyMessage}</div>}

      <section className="survey-grid" aria-label="Survey items">
        {surveyFilters.filteredItems.map((item) => (
          <article key={item.id} className="survey-card">
            <label className="survey-select">
              <input
                checked={selectedSurveyItemIds.includes(item.id)}
                type="checkbox"
                onChange={() => toggleSurveyItem(item.id)}
              />
              <span>Select</span>
            </label>
            <div className="survey-image">
              {isImageReference(item.image_url) ? (
                <img src={item.image_url} alt="" />
              ) : (
                <span>{item.brand.slice(0, 2).toUpperCase() || 'SB'}</span>
              )}
            </div>
            <div className="survey-card-copy">
              <h2>{item.item_name}</h2>
              <p>{item.item_description || 'No description provided yet.'}</p>
            </div>
            <div className="survey-inline-response">
              <fieldset>
                <legend>Interest</legend>
                {(['Interested', 'Not Interested'] as const).map((interest) => (
                  <label key={interest}>
                    <input
                      checked={getSurveyResponse(item.id).interest === interest}
                      type="checkbox"
                      value={interest}
                      onChange={() => {
                        if (!selectedSurveyItemIds.includes(item.id)) toggleSurveyItem(item.id)
                        updateSurveyResponse(item.id, {
                          interest: getSurveyResponse(item.id).interest === interest ? '' : interest,
                        })
                      }}
                    />
                    {interest}
                  </label>
                ))}
              </fieldset>
              <label>
                Feedback
                <textarea
                  value={getSurveyResponse(item.id).notes}
                  onChange={(event) => updateSurveyResponse(item.id, { notes: event.target.value })}
                  placeholder="Please provide feedback about the design, size, color, etc. of this item"
                />
              </label>
            </div>
          </article>
        ))}
        {!surveyLoading && surveyFilters.filteredItems.length === 0 && (
          <div className="empty-state">No survey items found.</div>
        )}
      </section>
    </main>
  )
}

export function ViewerPage({
  isPublicKanbanPath,
  publicQuery,
  publicStatusFilter,
  publicStatusOptions,
  publicLoading,
  publicError,
  publicItems,
  publicGridItems,
  publicKanbanColumns,
  setPublicQuery,
  setPublicStatusFilter,
  goToPath,
}: ViewerPageProps) {
  return (
    <main className="app-shell viewer-shell">
      <header className="brand-header">
        <div className="brand-topline">
          <img className="smartbuy-logo" src={smartBuyLogo} alt="SmartBuy" />
          <span>Public Viewer</span>
        </div>
        <nav className="app-nav" aria-label="Viewer navigation">
          <button
            className={!isPublicKanbanPath ? 'active' : ''}
            type="button"
            onClick={() => goToPath('/viewer')}
          >
            Grid View
          </button>
          <button
            className={isPublicKanbanPath ? 'active' : ''}
            type="button"
            onClick={() => goToPath('/viewer/kanban')}
          >
            Kanban Board
          </button>
        </nav>
      </header>

      <section className="client-title-row viewer-title-row">
        <div className="client-title-copy">
          <div className="client-title-divider" />
          <div>
            <p className="eyebrow">Order Interest Viewer</p>
            <h1>{isPublicKanbanPath ? 'Viewer Kanban' : 'Viewer Grid'}</h1>
            <p className="subtitle">Ad Hoc and Buy period items summarized for public review.</p>
          </div>
        </div>
        <img className="phillips-logo" src={phillipsLogo} alt="Phillips Distilling Co" />
      </section>

      <section className="viewer-toolbar">
        <label>
          Search
          <input
            value={publicQuery}
            onChange={(event) => setPublicQuery(event.target.value)}
            placeholder="Brand, program, item, notes, tracking..."
          />
        </label>
        {!isPublicKanbanPath && (
          <label>
            Status
            <select
              value={publicStatusFilter}
              onChange={(event) => setPublicStatusFilter(event.target.value)}
            >
              <option value="">All statuses</option>
              {publicStatusOptions.map((statusLabel) => (
                <option key={statusLabel} value={statusLabel}>
                  {statusLabel}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="viewer-toolbar-actions">
          <button
            className="secondary-action"
            type="button"
            onClick={() => {
              setPublicQuery('')
              setPublicStatusFilter('')
            }}
          >
            Reset Filters
          </button>
          <span>
            {publicLoading
              ? 'Loading...'
              : `${(isPublicKanbanPath ? publicItems : publicGridItems).length.toLocaleString()} items`}
          </span>
        </div>
      </section>

      {publicError && <div className="error">{publicError}</div>}

      {isPublicKanbanPath ? (
        <section className="kanban-board" aria-label="Viewer kanban board">
          {publicKanbanColumns.map(({ column, items }) => (
            <div key={column.id} className="kanban-column">
              <div className="kanban-column-header">
                <h2>{column.title}</h2>
                <span>{items.length}</span>
              </div>
              <div className="kanban-column-cards">
                {items.map((item) => (
                  <PublicCard key={item.id} item={item} />
                ))}
                {!publicLoading && items.length === 0 && (
                  <div className="empty-state">No items in this lane.</div>
                )}
              </div>
            </div>
          ))}
        </section>
      ) : (
        <section className="table-card viewer-grid-card" aria-label="Viewer grid">
          <div className="table-scroll">
            <table className="viewer-grid-table">
              <thead>
                <tr>
                  <th>Sheet</th>
                  <th>Status</th>
                  <th>Visual Reference</th>
                  <th>Brand</th>
                  <th>Program Name</th>
                  <th>Item Name</th>
                  <th>MRL Order #</th>
                  <th>Estimated Ship Date</th>
                  <th>Estimated IHD</th>
                  <th>Tracking</th>
                </tr>
              </thead>
              <tbody>
                {publicGridItems.map((item) => (
                  <tr key={item.id}>
                    <td>{publicSheetLabel(item.sheet)}</td>
                    <td>
                      <span className={`viewer-status-chip ${getViewerStatus(item)}`}>
                        {getViewerStatus(item) === 'on-track' ? 'On Track' : 'Not On Track'}
                      </span>
                      <span className="viewer-status-detail">{item.current_status || 'No Status'}</span>
                    </td>
                    <td>
                      {isImageReference(item.visual_reference) ? (
                        <img className="viewer-grid-image" src={item.visual_reference} alt="" />
                      ) : (
                        item.visual_reference || '-'
                      )}
                    </td>
                    <td>{item.brand || '-'}</td>
                    <td>{item.program_name || '-'}</td>
                    <td>{item.item_name || '-'}</td>
                    <td>{item.mrl_order_number || '-'}</td>
                    <td>{item.estimated_ship_date || '-'}</td>
                    <td>{item.estimated_ihd || '-'}</td>
                    <td>{item.tracking || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!publicLoading && publicGridItems.length === 0 && (
            <div className="empty-state">No viewer items match these filters.</div>
          )}
        </section>
      )}
    </main>
  )
}
