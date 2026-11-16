import {
  useMemo,
  useState,
  type FormEvent,
  type MouseEvent as ReactMouseEvent,
} from 'react'
import { ConfirmModal } from '../components/ConfirmModal'
import { ImageLightbox } from '../components/ImageLightbox'
import {
  BASE_COLUMNS,
  COLUMN_WIDTH_STORAGE_KEY,
  DEFAULT_COLUMN_WIDTHS,
  EMPTY_ITEM_DRAFT,
  SHEET_ORDER,
} from '../constants'
import type {
  AppUser,
  ColumnKey,
  CompanyInfo,
  EditableField,
  ItemDraft,
  PendingUpdates,
  RowPageSize,
  SheetKey,
  SheetMeta,
  SortDirection,
  TrackerItem,
} from '../types'
import type { AdminCompanyFilter } from '../utils/adminCompany'
import { isImageReference } from '../utils'

type TrackerPageProps = {
  currentUser: AppUser
  sheets: Record<SheetKey, SheetMeta> | null
  activeSheet: SheetKey
  items: TrackerItem[]
  loading: boolean
  error: string
  query: string
  statusFilter: string
  pendingCreates: TrackerItem[]
  pendingUpdates: PendingUpdates
  pendingDeletes: number[]
  isSubmittingChanges: boolean
  uploadingVisualReference: string | null
  companies: CompanyInfo[]
  companyFilter: AdminCompanyFilter
  onCompanyFilterChange: (value: AdminCompanyFilter) => void
  apiFetch: (path: string, options?: RequestInit) => Promise<Response>
  setActiveSheet: (sheet: SheetKey) => void
  setQuery: (q: string) => void
  setStatusFilter: (s: string) => void
  setCurrentTablePage: (p: number) => void
  updateDraft: (itemId: number, field: EditableField, value: string) => void
  deleteRow: (itemId: number) => void
  submitChanges: () => void
  openPublicPath: (path: string) => void
  setUploadingVisualReference: (key: string | null) => void
  setError: (msg: string) => void
  setPendingCreates: React.Dispatch<React.SetStateAction<TrackerItem[]>>
  setPendingUpdates: React.Dispatch<React.SetStateAction<PendingUpdates>>
  setPendingDeletes: React.Dispatch<React.SetStateAction<number[]>>
  setItems: React.Dispatch<React.SetStateAction<TrackerItem[]>>
  setRefreshItemsToken: React.Dispatch<React.SetStateAction<number>>
}

export function TrackerPage({
  currentUser,
  sheets,
  activeSheet,
  items,
  loading,
  error,
  query,
  statusFilter,
  pendingCreates,
  pendingUpdates,
  pendingDeletes,
  isSubmittingChanges,
  uploadingVisualReference,
  companies,
  companyFilter,
  onCompanyFilterChange,
  apiFetch,
  setActiveSheet,
  setQuery,
  setStatusFilter,
  setCurrentTablePage,
  updateDraft,
  deleteRow: _deleteRow,
  submitChanges,
  openPublicPath,
  setUploadingVisualReference,
  setError,
  setPendingCreates,
  setPendingUpdates,
  setPendingDeletes,
  setItems,
  setRefreshItemsToken: _setRefreshItemsToken,
}: TrackerPageProps) {
  const isAdmin = currentUser.role === 'admin'
  const viewingAllCompanies = isAdmin && companyFilter === 'all'
  const [rowPageSize, setRowPageSize] = useState<RowPageSize>(20)
  const [currentTablePage, setLocalTablePage] = useState(1)
  const [sortField, setSortField] = useState<EditableField>('date_or_buy')
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc')
  const [isAddFormOpen, setIsAddFormOpen] = useState(false)
  const [addForm, setAddForm] = useState<ItemDraft>(EMPTY_ITEM_DRAFT)
  const [previewImage, setPreviewImage] = useState<{ url: string; title: string } | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null)
  const [columnWidths, setColumnWidths] = useState<Record<ColumnKey, number>>(() => {
    const saved = window.localStorage.getItem(COLUMN_WIDTH_STORAGE_KEY)
    if (!saved) return DEFAULT_COLUMN_WIDTHS
    try { return { ...DEFAULT_COLUMN_WIDTHS, ...JSON.parse(saved) } } catch { return DEFAULT_COLUMN_WIDTHS }
  })

  const activeStatuses = sheets?.[activeSheet]?.statuses ?? []
  const columns = useMemo(() =>
    BASE_COLUMNS.map((col) =>
      col.field === 'date_or_buy'
        ? { ...col, label: sheets?.[activeSheet]?.dateLabel ?? col.label }
        : col
    ), [activeSheet, sheets])

  const pendingUpdateCount = Object.keys(pendingUpdates).length
  const pendingChangeCount = pendingCreates.length + pendingUpdateCount + pendingDeletes.length
  const hasPendingChanges = pendingChangeCount > 0

  const companyColumnWidth = 160
  const tableWidth = useMemo(() =>
    Object.values(columnWidths).reduce((t, w) => t + w, 0) + (viewingAllCompanies ? companyColumnWidth : 0),
  [columnWidths, viewingAllCompanies])

  const sortedItems = useMemo(() => {
    return [...items].sort((a, b) => {
      const av = a[sortField] ?? '', bv = b[sortField] ?? ''
      const ad = Date.parse(av), bd = Date.parse(bv)
      const an = Number(av.replace(/,/g, '')), bn = Number(bv.replace(/,/g, ''))
      let result: number
      if (!isNaN(ad) && !isNaN(bd)) result = ad - bd
      else if (av.trim() && bv.trim() && !isNaN(an) && !isNaN(bn)) result = an - bn
      else result = av.localeCompare(bv, undefined, { numeric: true, sensitivity: 'base' })
      return sortDirection === 'asc' ? result : -result
    })
  }, [items, sortDirection, sortField])

  const tablePageCount = rowPageSize === 'all' ? 1 : Math.max(1, Math.ceil(items.length / rowPageSize))
  const activePage = Math.min(currentTablePage, tablePageCount)
  const visibleItems = useMemo(() => {
    if (rowPageSize === 'all') return sortedItems
    const start = (activePage - 1) * rowPageSize
    return sortedItems.slice(start, start + rowPageSize)
  }, [activePage, rowPageSize, sortedItems])

  const visibleStart = items.length === 0 ? 0 : (activePage - 1) * (rowPageSize === 'all' ? items.length : rowPageSize) + 1
  const visibleEnd = rowPageSize === 'all' ? items.length : Math.min(items.length, activePage * rowPageSize)

  const statusCounts = useMemo(() =>
    items.reduce<Record<string, number>>((acc, item) => {
      const label = item.current_status || 'No Status'
      acc[label] = (acc[label] ?? 0) + 1
      return acc
    }, {}), [items])

  function openAddForm() {
    if (viewingAllCompanies) {
      setError('Select a company dataset before adding tracker rows.')
      return
    }
    setAddForm({ ...EMPTY_ITEM_DRAFT, current_status: sheets?.[activeSheet]?.statuses[0] ?? '' })
    setIsAddFormOpen(true)
  }

  function addDraftRow(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const draft: TrackerItem = {
      id: -Date.now(), sheet: activeSheet, source_row: undefined,
      ...addForm, extra: {}, updated_at: new Date().toISOString(),
    }
    setItems((c) => [...c, draft])
    setPendingCreates((c) => [...c, draft])
    setIsAddFormOpen(false)
    setLocalTablePage(1)
  }

  function handleDeleteRow(itemId: number) {
    setConfirmDelete(itemId)
  }

  function confirmDeleteRow() {
    if (confirmDelete === null) return
    setItems((c) => c.filter((item) => item.id !== confirmDelete))
    if (confirmDelete < 0) {
      setPendingCreates((c) => c.filter((item) => item.id !== confirmDelete))
    } else {
      setPendingDeletes((c) => c.includes(confirmDelete) ? c : [...c, confirmDelete])
      setPendingUpdates((c) => { const next = { ...c }; delete next[confirmDelete]; return next })
    }
    setConfirmDelete(null)
  }

  async function uploadVisualReference(file: File, uploadKey: string, applyUrl: (url: string) => void) {
    if (!file.type.startsWith('image/')) { setError('Please upload a picture file.'); return }
    const formData = new FormData()
    formData.append('file', file)
    setUploadingVisualReference(uploadKey)
    setError('')
    try {
      const res = await apiFetch('/uploads/visual-reference', { method: 'POST', body: formData })
      if (!res.ok) {
        throw new Error(res.status === 500 ? 'Cloudinary is not configured yet.' : 'Unable to upload that image.')
      }
      const data = await res.json()
      applyUrl(data.url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to upload that image.')
    } finally {
      setUploadingVisualReference(null)
    }
  }

  async function handleExport() {
    const params = new URLSearchParams({ sheet: activeSheet })
    if (query.trim()) params.set('q', query.trim())
    if (statusFilter) params.set('status', statusFilter)
    if (isAdmin && typeof companyFilter === 'number') params.set('company_id', String(companyFilter))
    try {
      const res = await apiFetch(`/items/export?${params}`)
      if (!res.ok) throw new Error('Export failed.')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `tracker-${activeSheet}-${new Date().toISOString().slice(0, 10)}.xlsx`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed.')
    }
  }

  function resizeColumn(column: ColumnKey, event: ReactMouseEvent<HTMLButtonElement>) {
    event.preventDefault()
    const startX = event.clientX
    const startWidth = columnWidths[column]
    function onMove(e: globalThis.MouseEvent) {
      const next = Math.max(80, startWidth + e.clientX - startX)
      setColumnWidths((c) => { const u = { ...c, [column]: next }; window.localStorage.setItem(COLUMN_WIDTH_STORAGE_KEY, JSON.stringify(u)); return u })
    }
    function onUp() {
      document.body.classList.remove('is-resizing-column')
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    document.body.classList.add('is-resizing-column')
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  function sortByColumn(field: EditableField) {
    if (sortField === field) setSortDirection((d) => d === 'asc' ? 'desc' : 'asc')
    else { setSortField(field); setSortDirection('asc') }
    setLocalTablePage(1)
  }

  function changeRowPageSize(value: string) {
    setRowPageSize(value === 'all' ? 'all' : Number(value) as RowPageSize)
    setLocalTablePage(1)
  }

  function renderCell(item: TrackerItem, column: typeof columns[0]) {
    const value = item[column.field] ?? ''
    if (column.field === 'current_status') {
      return (
        <select value={value} onChange={(e) => updateDraft(item.id, column.field, e.target.value)}>
          <option value="">No Status</option>
          {activeStatuses.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      )
    }
    if (column.field === 'visual_reference') {
      return (
        <div className="visual-reference-field">
          {isImageReference(value) && (
            <button className="visual-reference-preview" type="button"
              onClick={() => setPreviewImage({ url: value, title: item.item_name || 'Visual reference' })}>
              <img src={value} alt="Visual reference preview" />
            </button>
          )}
          <input value={value} onChange={(e) => updateDraft(item.id, column.field, e.target.value)} />
          <div className="visual-reference-actions">
            <label className="upload-chip">
              {uploadingVisualReference === `${item.id}-vr` ? 'Uploading...' : 'Upload'}
              <input accept="image/jpeg,image/png,image/webp" type="file"
                disabled={uploadingVisualReference === `${item.id}-vr`}
                onChange={(e) => {
                  const file = e.target.files?.[0]; e.target.value = ''
                  if (file) uploadVisualReference(file, `${item.id}-vr`, (url) => updateDraft(item.id, column.field, url))
                }} />
            </label>
            {value && <a href={value} target="_blank" rel="noreferrer">Preview</a>}
          </div>
        </div>
      )
    }
    if (column.field === 'important_notes' || column.field === 'tracking') {
      return <textarea value={value} onChange={(e) => updateDraft(item.id, column.field, e.target.value)} />
    }
    return (
      <input
        type={column.field === 'date_or_buy' && activeSheet === 'ad-hoc' ? 'date' : 'text'}
        value={value}
        onChange={(e) => updateDraft(item.id, column.field, e.target.value)}
      />
    )
  }

  return (
    <>
      {isAdmin && (
        <div className="admin-company-filter">
          <label>
            Company dataset
            <select
              value={companyFilter === 'all' ? 'all' : String(companyFilter)}
              onChange={(e) => {
                const value = e.target.value
                onCompanyFilterChange(value === 'all' ? 'all' : Number(value))
                setLocalTablePage(1)
              }}
            >
              <option value="all">All companies</option>
              {companies.map((company) => (
                <option key={company.id} value={company.id}>
                  {company.company_name || company.name}
                </option>
              ))}
            </select>
          </label>
          <span className="admin-company-filter-note">
            Defaults to all companies. Pick one company to add new rows for that dataset.
          </span>
        </div>
      )}

      <div className="tracker-actions">
        <button className="primary-action" type="button" onClick={openAddForm}>Add</button>
        <button className="secondary-action" type="button" onClick={() => openPublicPath('/viewer/kanban')}>
          Viewer Kanban
        </button>
        <button className="secondary-action" type="button" onClick={handleExport}>
          Export Excel
        </button>
        <button
          className={`submit-action ${hasPendingChanges ? 'ready' : ''}`}
          disabled={!hasPendingChanges || isSubmittingChanges}
          type="button"
          onClick={submitChanges}
        >
          {isSubmittingChanges ? 'Submitting...' : hasPendingChanges ? `Submit Changes (${pendingChangeCount})` : 'Submit Changes'}
        </button>
      </div>

      <section className="tabs" aria-label="Tracker sheets">
        {SHEET_ORDER.map((sheet) => (
          <button key={sheet} className={sheet === activeSheet ? 'active' : ''} type="button"
            onClick={() => { setActiveSheet(sheet); setStatusFilter(''); setCurrentTablePage(1); setLocalTablePage(1) }}>
            {sheets?.[sheet]?.label ?? sheet}
          </button>
        ))}
      </section>

      <section className="toolbar">
        <label>
          Search
          <input value={query} placeholder="Brand, program, item, notes, tracking..."
            onChange={(e) => { setQuery(e.target.value); setLocalTablePage(1) }} />
        </label>
        <label>
          Status
          <select value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setLocalTablePage(1) }}>
            <option value="">All statuses</option>
            {activeStatuses.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <button className="secondary-action" type="button"
          onClick={() => { setQuery(''); setStatusFilter(''); setLocalTablePage(1) }}>
          Reset Filters
        </button>
        <div className="row-count">{loading ? 'Loading...' : `${items.length.toLocaleString()} rows`}</div>
      </section>

      {error && <div className="error">{error}</div>}

      {isAddFormOpen && (
        <div className="modal-backdrop" role="presentation">
          <form className="modal-card" onSubmit={addDraftRow}>
            <div className="modal-header">
              <div>
                <p className="eyebrow">New Tracker Item</p>
                <h2>Add {sheets?.[activeSheet]?.label ?? 'Tracker'} Row</h2>
              </div>
              <button type="button" onClick={() => setIsAddFormOpen(false)}>Close</button>
            </div>
            <div className="add-form-grid">
              {columns.map((col) => (
                <label key={col.field}>
                  {col.label}
                  {col.field === 'current_status' ? (
                    <select value={addForm.current_status}
                      onChange={(e) => setAddForm((c) => ({ ...c, current_status: e.target.value }))}>
                      <option value="">No Status</option>
                      {activeStatuses.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                  ) : col.field === 'visual_reference' ? (
                    <div className="visual-reference-field">
                      {isImageReference(addForm.visual_reference) && (
                        <button className="visual-reference-preview" type="button"
                          onClick={() => setPreviewImage({ url: addForm.visual_reference, title: addForm.item_name || 'Visual reference' })}>
                          <img src={addForm.visual_reference} alt="Visual reference preview" />
                        </button>
                      )}
                      <input value={addForm.visual_reference}
                        onChange={(e) => setAddForm((c) => ({ ...c, visual_reference: e.target.value }))}
                        placeholder="Paste a URL or upload an image" />
                      <div className="visual-reference-actions">
                        <label className="upload-chip">
                          {uploadingVisualReference === 'add-vr' ? 'Uploading...' : 'Upload Image'}
                          <input accept="image/jpeg,image/png,image/webp" type="file"
                            disabled={uploadingVisualReference === 'add-vr'}
                            onChange={(e) => {
                              const file = e.target.files?.[0]; e.target.value = ''
                              if (file) uploadVisualReference(file, 'add-vr', (url) => setAddForm((c) => ({ ...c, visual_reference: url })))
                            }} />
                        </label>
                        {addForm.visual_reference && <a href={addForm.visual_reference} target="_blank" rel="noreferrer">Preview</a>}
                      </div>
                    </div>
                  ) : col.field === 'important_notes' || col.field === 'tracking' ? (
                    <textarea value={addForm[col.field]}
                      onChange={(e) => setAddForm((c) => ({ ...c, [col.field]: e.target.value }))} />
                  ) : (
                    <input
                      type={col.field === 'date_or_buy' && activeSheet === 'ad-hoc' ? 'date' : 'text'}
                      value={addForm[col.field]}
                      onChange={(e) => setAddForm((c) => ({ ...c, [col.field]: e.target.value }))} />
                  )}
                </label>
              ))}
            </div>
            <div className="modal-actions">
              <button type="button" onClick={() => setIsAddFormOpen(false)}>Cancel</button>
              <button className="primary-action" type="submit">Add Draft</button>
            </div>
          </form>
        </div>
      )}

      <section className="status-summary" aria-label="Status summary">
        {Object.entries(statusCounts).map(([status, count]) => (
          <button key={status} type="button"
            onClick={() => { setStatusFilter(status === 'No Status' ? '' : status); setLocalTablePage(1) }}>
            <strong>{count}</strong>
            <span>{status}</span>
          </button>
        ))}
      </section>

      <section className="table-card">
        <div className="table-tools">
          <span>Drag the right edge of any column header to resize it.</span>
          <div className="table-tool-actions">
            <label>Rows
              <select value={rowPageSize} onChange={(e) => changeRowPageSize(e.target.value)}>
                <option value={10}>10</option>
                <option value={20}>20</option>
                <option value={30}>30</option>
                <option value="all">All</option>
              </select>
            </label>
            <button type="button" onClick={() => { setColumnWidths(DEFAULT_COLUMN_WIDTHS); window.localStorage.removeItem(COLUMN_WIDTH_STORAGE_KEY) }}>
              Reset Columns
            </button>
          </div>
        </div>
        <div className="table-scroll">
          <table style={{ minWidth: tableWidth }}>
            <colgroup>
              {viewingAllCompanies && <col style={{ width: companyColumnWidth }} />}
              {columns.map((col) => <col key={col.field} style={{ width: columnWidths[col.field] }} />)}
              <col style={{ width: columnWidths.actions }} />
            </colgroup>
            <thead>
              <tr>
                {viewingAllCompanies && (
                  <th>
                    <span>Company</span>
                  </th>
                )}
                {columns.map((col) => (
                  <th key={col.field} className={col.className}>
                    <button className="column-sort" type="button"
                      aria-label={`Sort by ${col.label}`}
                      aria-sort={sortField === col.field ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
                      onClick={() => sortByColumn(col.field)}>
                      <span>{col.label}</span>
                      <span className="sort-arrows" aria-hidden="true">
                        <span className={sortField === col.field && sortDirection === 'asc' ? 'active' : ''}>▲</span>
                        <span className={sortField === col.field && sortDirection === 'desc' ? 'active' : ''}>▼</span>
                      </span>
                    </button>
                    <button className="column-resizer" aria-label={`Resize ${col.label} column`} type="button"
                      onMouseDown={(e) => resizeColumn(col.field, e)} />
                  </th>
                ))}
                <th className="actions">
                  <span>Actions</span>
                  <button className="column-resizer" aria-label="Resize Actions column" type="button"
                    onMouseDown={(e) => resizeColumn('actions', e)} />
                </th>
              </tr>
            </thead>
            <tbody>
              {!loading && visibleItems.map((item) => (
                <tr key={item.id}>
                  {viewingAllCompanies && (
                    <td>
                      <strong>{item.company_name || '—'}</strong>
                    </td>
                  )}
                  {columns.map((col) => (
                    <td key={col.field} className={col.className}>
                      {renderCell(item, col)}
                      {item.id < 0 && <span className="saving">New draft</span>}
                      {item.id > 0 && pendingUpdates[item.id]?.[col.field] !== undefined && <span className="saving">Unsaved</span>}
                    </td>
                  ))}
                  <td className="actions">
                    {isAdmin
                      ? <button type="button" onClick={() => handleDeleteRow(item.id)}>Delete</button>
                      : <span className="muted">Admin only</span>}
                  </td>
                </tr>
              ))}
              {!loading && items.length === 0 && (
                <tr>
                  <td colSpan={columns.length + 1 + (viewingAllCompanies ? 1 : 0)} className="empty-state">
                    No rows match the current filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {!loading && items.length > 0 && (
          <div className="table-pagination">
            <span>Showing {visibleStart.toLocaleString()}–{visibleEnd.toLocaleString()} of {items.length.toLocaleString()} rows</span>
            {rowPageSize !== 'all' && (
              <div className="pagination-actions">
                <button disabled={activePage === 1} type="button" onClick={() => setLocalTablePage((p) => Math.max(1, p - 1))}>Previous</button>
                <span>Page {activePage} of {tablePageCount}</span>
                <button disabled={activePage === tablePageCount} type="button" onClick={() => setLocalTablePage((p) => Math.min(tablePageCount, p + 1))}>Next</button>
              </div>
            )}
          </div>
        )}
      </section>

      {previewImage && <ImageLightbox url={previewImage.url} title={previewImage.title} onClose={() => setPreviewImage(null)} />}
      {confirmDelete !== null && (
        <ConfirmModal
          title="Delete Row"
          message="Are you sure you want to delete this tracker row? This cannot be undone."
          confirmLabel="Delete"
          danger
          onConfirm={confirmDeleteRow}
          onCancel={() => setConfirmDelete(null)}
        />
      )}
    </>
  )
}
