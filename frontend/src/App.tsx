import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type MouseEvent as ReactMouseEvent,
} from 'react'
import phillipsLogo from './assets/3184_Phillips Store Logo.png'
import smartBuyLogo from './assets/SmartBuy Logo Transparent.png'
import './App.css'

type SheetKey = 'ad-hoc' | 'buys' | 'completed'
type Role = 'admin' | 'editor'
type Page = 'tracker' | 'admin-users' | 'system-log'
type RowPageSize = 10 | 20 | 30 | 'all'
type SortDirection = 'asc' | 'desc'

type SheetMeta = {
  label: string
  dateLabel: string
  statuses: string[]
}

type TrackerItem = {
  id: number
  sheet: SheetKey
  source_row?: number
  date_or_buy: string
  current_status: string
  visual_reference: string
  brand: string
  program_name: string
  item_name: string
  qty: string
  important_notes: string
  mrl_order_number: string
  estimated_ship_date: string
  estimated_ihd: string
  tracking: string
  extra?: Record<string, string>
  updated_at: string
}

type EditableField = Exclude<
  keyof TrackerItem,
  'id' | 'sheet' | 'source_row' | 'extra' | 'updated_at'
>

type ColumnKey = EditableField | 'actions'
type ItemDraft = Record<EditableField, string>
type PendingUpdates = Record<number, Partial<ItemDraft>>

type AuditLog = {
  id: number
  user_id: number | null
  user_email: string
  action: string
  sheet: string | null
  item_id: number | null
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  created_at: string
}

type AppUser = {
  id: number
  email: string
  name: string
  role: Role
  is_active: boolean
  created_at: string
  updated_at: string
}

const API_BASE = '/api'
const AUTH_STORAGE_KEY = 'phillips-tracker-auth-token'
const SHEET_ORDER: SheetKey[] = ['ad-hoc', 'buys', 'completed']
const COLUMN_WIDTH_STORAGE_KEY = 'phillips-tracker-column-widths'

const BASE_COLUMNS: { field: EditableField; label: string; width: number; className?: string }[] = [
  { field: 'date_or_buy', label: 'Date/Buy', width: 140 },
  { field: 'current_status', label: 'Current Status', width: 190 },
  { field: 'visual_reference', label: 'Visual Reference', width: 170 },
  { field: 'brand', label: 'Brand', width: 150 },
  { field: 'program_name', label: 'Program Name', width: 300, className: 'wide' },
  { field: 'item_name', label: 'Item Name', width: 300, className: 'wide' },
  { field: 'qty', label: 'Qty', width: 110 },
  { field: 'important_notes', label: 'Important Notes', width: 360, className: 'notes' },
  { field: 'mrl_order_number', label: 'MRL Order #', width: 150 },
  { field: 'estimated_ship_date', label: 'Estimated Ship Date', width: 190 },
  { field: 'estimated_ihd', label: 'Estimated IHD', width: 170 },
  { field: 'tracking', label: 'Tracking', width: 340, className: 'notes' },
]

const DEFAULT_COLUMN_WIDTHS: Record<ColumnKey, number> = {
  ...Object.fromEntries(BASE_COLUMNS.map((column) => [column.field, column.width])),
  actions: 110,
} as Record<ColumnKey, number>

const EMPTY_ITEM_DRAFT: ItemDraft = {
  date_or_buy: '',
  current_status: '',
  visual_reference: '',
  brand: '',
  program_name: '',
  item_name: '',
  qty: '',
  important_notes: '',
  mrl_order_number: '',
  estimated_ship_date: '',
  estimated_ihd: '',
  tracking: '',
}

function App() {
  const [token, setToken] = useState(() => window.localStorage.getItem(AUTH_STORAGE_KEY) ?? '')
  const [currentUser, setCurrentUser] = useState<AppUser | null>(null)
  const [page, setPage] = useState<Page>('tracker')
  const [authChecking, setAuthChecking] = useState(Boolean(token))
  const [sheets, setSheets] = useState<Record<SheetKey, SheetMeta> | null>(null)
  const [activeSheet, setActiveSheet] = useState<SheetKey>('ad-hoc')
  const [items, setItems] = useState<TrackerItem[]>([])
  const [users, setUsers] = useState<AppUser[]>([])
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([])
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [rowPageSize, setRowPageSize] = useState<RowPageSize>(20)
  const [currentTablePage, setCurrentTablePage] = useState(1)
  const [sortField, setSortField] = useState<EditableField>('date_or_buy')
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc')
  const [pendingCreates, setPendingCreates] = useState<TrackerItem[]>([])
  const [pendingUpdates, setPendingUpdates] = useState<PendingUpdates>({})
  const [pendingDeletes, setPendingDeletes] = useState<number[]>([])
  const [isAddFormOpen, setIsAddFormOpen] = useState(false)
  const [addForm, setAddForm] = useState<ItemDraft>(EMPTY_ITEM_DRAFT)
  const [refreshItemsToken, setRefreshItemsToken] = useState(0)
  const [loading, setLoading] = useState(true)
  const [usersLoading, setUsersLoading] = useState(false)
  const [logsLoading, setLogsLoading] = useState(false)
  const [isSubmittingChanges, setIsSubmittingChanges] = useState(false)
  const [uploadingVisualReference, setUploadingVisualReference] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [authError, setAuthError] = useState('')
  const [adminMessage, setAdminMessage] = useState('')
  const [loginForm, setLoginForm] = useState({ email: 'admin@example.com', password: 'Admin123' })
  const [showNewUserPassword, setShowNewUserPassword] = useState(false)
  const [newUser, setNewUser] = useState({
    email: '',
    name: '',
    password: '',
    role: 'editor' as Role,
    is_active: true,
  })
  const [columnWidths, setColumnWidths] = useState<Record<ColumnKey, number>>(() => {
    const saved = window.localStorage.getItem(COLUMN_WIDTH_STORAGE_KEY)
    if (!saved) return DEFAULT_COLUMN_WIDTHS

    try {
      return { ...DEFAULT_COLUMN_WIDTHS, ...JSON.parse(saved) }
    } catch {
      return DEFAULT_COLUMN_WIDTHS
    }
  })

  const columns = useMemo(() => {
    return BASE_COLUMNS.map((column) =>
      column.field === 'date_or_buy'
        ? { ...column, label: sheets?.[activeSheet]?.dateLabel ?? column.label }
        : column,
    )
  }, [activeSheet, sheets])

  const handleLogout = useCallback(() => {
    window.localStorage.removeItem(AUTH_STORAGE_KEY)
    setToken('')
    setCurrentUser(null)
    setSheets(null)
    setItems([])
    setUsers([])
    setAuditLogs([])
    setPendingCreates([])
    setPendingUpdates({})
    setPendingDeletes([])
    setPage('tracker')
  }, [])

  const apiFetch = useCallback(async (path: string, options: RequestInit = {}) => {
    const headers = new Headers(options.headers)
    if (!headers.has('Content-Type') && options.body && !(options.body instanceof FormData)) {
      headers.set('Content-Type', 'application/json')
    }
    if (token) headers.set('Authorization', `Bearer ${token}`)

    const response = await fetch(`${API_BASE}${path}`, { ...options, headers })
    if (response.status === 401) {
      handleLogout()
      throw new Error('Please log in again.')
    }
    return response
  }, [handleLogout, token])

  function acceptLogin(nextToken: string, user: AppUser) {
    window.localStorage.setItem(AUTH_STORAGE_KEY, nextToken)
    setToken(nextToken)
    setCurrentUser(user)
    setAuthError('')
    setPage('tracker')
  }

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAuthError('')

    const response = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(loginForm),
    })
    if (!response.ok) {
      setAuthError('Invalid email or password.')
      return
    }

    const data = await response.json()
    acceptLogin(data.token, data.user)
  }

  useEffect(() => {
    if (!token) {
      return
    }

    async function loadCurrentUser() {
      setAuthChecking(true)
      try {
        const response = await fetch(`${API_BASE}/auth/me`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!response.ok) throw new Error('Stored login expired.')
        const data = await response.json()
        setCurrentUser(data.user)
      } catch {
        handleLogout()
      } finally {
        setAuthChecking(false)
      }
    }

    loadCurrentUser()
  }, [handleLogout, token])

  useEffect(() => {
    if (!currentUser) return

    async function loadSheets() {
      const response = await apiFetch('/sheets')
      if (!response.ok) throw new Error('Unable to load sheet configuration.')
      const data = await response.json()
      setSheets(data.sheets)
    }

    loadSheets().catch((err: Error) => setError(err.message))
  }, [apiFetch, currentUser])

  useEffect(() => {
    if (!currentUser || page !== 'tracker') return

    const controller = new AbortController()

    async function loadItems() {
      setLoading(true)
      setError('')
      const params = new URLSearchParams({ sheet: activeSheet })
      if (query.trim()) params.set('q', query.trim())
      if (statusFilter) params.set('status', statusFilter)

      const response = await apiFetch(`/items?${params}`, {
        signal: controller.signal,
      })
      if (!response.ok) throw new Error('Unable to load tracker rows.')
      const data = await response.json()
      setItems(data.items)
      setLoading(false)
    }

    loadItems().catch((err: Error) => {
      if (err.name === 'AbortError') return
      setError(err.message)
      setLoading(false)
    })

    return () => controller.abort()
  }, [activeSheet, apiFetch, currentUser, page, query, refreshItemsToken, statusFilter])

  const loadUsers = useCallback(async () => {
    setUsersLoading(true)
    setError('')
    try {
      const response = await apiFetch('/admin/users')
      if (!response.ok) throw new Error('Unable to load users.')
      const data = await response.json()
      setUsers(data.users)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load users.')
    } finally {
      setUsersLoading(false)
    }
  }, [apiFetch])

  useEffect(() => {
    if (!currentUser || currentUser.role !== 'admin' || page !== 'admin-users') return

    const timeoutId = window.setTimeout(() => {
      loadUsers()
    }, 0)

    return () => window.clearTimeout(timeoutId)
  }, [currentUser, loadUsers, page])

  const loadAuditLogs = useCallback(async () => {
    setLogsLoading(true)
    setError('')
    try {
      const response = await apiFetch('/admin/audit-logs')
      if (!response.ok) throw new Error('Unable to load system log.')
      const data = await response.json()
      setAuditLogs(data.logs)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load system log.')
    } finally {
      setLogsLoading(false)
    }
  }, [apiFetch])

  useEffect(() => {
    if (!currentUser || currentUser.role !== 'admin' || page !== 'system-log') return

    const timeoutId = window.setTimeout(() => {
      loadAuditLogs()
    }, 0)

    return () => window.clearTimeout(timeoutId)
  }, [currentUser, loadAuditLogs, page])

  function updateDraft(itemId: number, field: EditableField, value: string) {
    setItems((current) =>
      current.map((item) => (item.id === itemId ? { ...item, [field]: value } : item)),
    )
    if (itemId < 0) {
      setPendingCreates((current) =>
        current.map((item) => (item.id === itemId ? { ...item, [field]: value } : item)),
      )
      return
    }

    setPendingUpdates((current) => ({
      ...current,
      [itemId]: {
        ...(current[itemId] ?? {}),
        [field]: value,
      },
    }))
  }

  function openAddForm() {
    setAddForm({
      ...EMPTY_ITEM_DRAFT,
      current_status: sheets?.[activeSheet]?.statuses[0] ?? '',
    })
    setIsAddFormOpen(true)
  }

  function addDraftRow(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const draftItem: TrackerItem = {
      id: -Date.now(),
      sheet: activeSheet,
      source_row: undefined,
      ...addForm,
      extra: {},
      updated_at: new Date().toISOString(),
    }

    setItems((current) => [...current, draftItem])
    setPendingCreates((current) => [...current, draftItem])
    setIsAddFormOpen(false)
    setCurrentTablePage(1)
  }

  function deleteRow(itemId: number) {
    const confirmed = window.confirm('Delete this tracker row?')
    if (!confirmed) return

    setItems((current) => current.filter((item) => item.id !== itemId))
    if (itemId < 0) {
      setPendingCreates((current) => current.filter((item) => item.id !== itemId))
      return
    }

    setPendingDeletes((current) => (current.includes(itemId) ? current : [...current, itemId]))
    setPendingUpdates((current) => {
      const next = { ...current }
      delete next[itemId]
      return next
    })
  }

  async function uploadVisualReference(
    file: File,
    uploadKey: string,
    applyUrl: (url: string) => void,
  ) {
    if (!file.type.startsWith('image/')) {
      setError('Please upload a picture file.')
      return
    }

    const formData = new FormData()
    formData.append('file', file)
    setUploadingVisualReference(uploadKey)
    setError('')

    try {
      const response = await apiFetch('/uploads/visual-reference', {
        method: 'POST',
        body: formData,
      })
      if (!response.ok) {
        const message =
          response.status === 500
            ? 'Cloudinary is not configured yet.'
            : 'Unable to upload that image.'
        throw new Error(message)
      }

      const data = await response.json()
      applyUrl(data.url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to upload that image.')
    } finally {
      setUploadingVisualReference(null)
    }
  }

  async function submitChanges() {
    if (!hasPendingChanges) return

    setIsSubmittingChanges(true)
    setError('')
    try {
      const response = await apiFetch('/items/changes', {
        method: 'POST',
        body: JSON.stringify({
          creates: pendingCreates.map((item) => ({
            sheet: item.sheet,
            date_or_buy: item.date_or_buy,
            current_status: item.current_status,
            visual_reference: item.visual_reference,
            brand: item.brand,
            program_name: item.program_name,
            item_name: item.item_name,
            qty: item.qty,
            important_notes: item.important_notes,
            mrl_order_number: item.mrl_order_number,
            estimated_ship_date: item.estimated_ship_date,
            estimated_ihd: item.estimated_ihd,
            tracking: item.tracking,
          })),
          updates: Object.entries(pendingUpdates).map(([id, changes]) => ({
            id: Number(id),
            changes,
          })),
          deletes: pendingDeletes,
        }),
      })
      if (!response.ok) throw new Error('Unable to submit changes.')
      setPendingCreates([])
      setPendingUpdates({})
      setPendingDeletes([])
      setRefreshItemsToken((current) => current + 1)
      if (isAdmin) loadAuditLogs()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to submit changes.')
    } finally {
      setIsSubmittingChanges(false)
    }
  }

  async function createUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAdminMessage('')
    setError('')

    const response = await apiFetch('/admin/users', {
      method: 'POST',
      body: JSON.stringify(newUser),
    })
    if (!response.ok) {
      setError(response.status === 409 ? 'A user with that email already exists.' : 'Unable to create user.')
      return
    }

    const data = await response.json()
    setUsers((current) => [data.user, ...current])
    setNewUser({ email: '', name: '', password: '', role: 'editor', is_active: true })
    setAdminMessage('User created.')
  }

  async function updateUser(userId: number, updates: Partial<AppUser>) {
    setAdminMessage('')
    setError('')
    const response = await apiFetch(`/admin/users/${userId}`, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    })
    if (!response.ok) {
      setError('Unable to update user.')
      return
    }
    const data = await response.json()
    setUsers((current) => current.map((user) => (user.id === userId ? data.user : user)))
    if (currentUser?.id === userId) setCurrentUser(data.user)
    setAdminMessage('User updated.')
  }

  async function resetUserPassword(userId: number) {
    const password = window.prompt('Enter the new password for this user:')
    if (!password) return
    if (password.length < 6) {
      setError('Password must be at least 6 characters.')
      return
    }

    const response = await apiFetch(`/admin/users/${userId}/reset-password`, {
      method: 'POST',
      body: JSON.stringify({ password }),
    })
    if (!response.ok) {
      setError('Unable to reset password.')
      return
    }
    setAdminMessage('Password reset.')
  }

  const activeStatuses = sheets?.[activeSheet]?.statuses ?? []
  const pendingUpdateCount = Object.keys(pendingUpdates).length
  const pendingChangeCount = pendingCreates.length + pendingUpdateCount + pendingDeletes.length
  const hasPendingChanges = pendingChangeCount > 0
  const tableWidth = useMemo(() => {
    return Object.values(columnWidths).reduce((total, width) => total + width, 0)
  }, [columnWidths])

  const tablePageCount =
    rowPageSize === 'all' ? 1 : Math.max(1, Math.ceil(items.length / rowPageSize))
  const activeTablePage = Math.min(currentTablePage, tablePageCount)
  const sortedItems = useMemo(() => {
    return [...items].sort((firstItem, secondItem) => {
      const firstValue = firstItem[sortField] ?? ''
      const secondValue = secondItem[sortField] ?? ''
      const firstDate = Date.parse(firstValue)
      const secondDate = Date.parse(secondValue)
      const firstNumber = Number(firstValue.replace(/,/g, ''))
      const secondNumber = Number(secondValue.replace(/,/g, ''))

      let result: number
      if (!Number.isNaN(firstDate) && !Number.isNaN(secondDate)) {
        result = firstDate - secondDate
      } else if (
        firstValue.trim() !== '' &&
        secondValue.trim() !== '' &&
        !Number.isNaN(firstNumber) &&
        !Number.isNaN(secondNumber)
      ) {
        result = firstNumber - secondNumber
      } else {
        result = firstValue.localeCompare(secondValue, undefined, {
          numeric: true,
          sensitivity: 'base',
        })
      }

      return sortDirection === 'asc' ? result : -result
    })
  }, [items, sortDirection, sortField])

  const visibleItems = useMemo(() => {
    if (rowPageSize === 'all') return sortedItems

    const start = (activeTablePage - 1) * rowPageSize
    return sortedItems.slice(start, start + rowPageSize)
  }, [activeTablePage, rowPageSize, sortedItems])
  const visibleStart = items.length === 0 ? 0 : (activeTablePage - 1) * (rowPageSize === 'all' ? items.length : rowPageSize) + 1
  const visibleEnd =
    rowPageSize === 'all' ? items.length : Math.min(items.length, activeTablePage * rowPageSize)

  const statusCounts = useMemo(() => {
    return items.reduce<Record<string, number>>((counts, item) => {
      const label = item.current_status || 'No Status'
      counts[label] = (counts[label] ?? 0) + 1
      return counts
    }, {})
  }, [items])

  function resizeColumn(column: ColumnKey, event: ReactMouseEvent<HTMLButtonElement>) {
    event.preventDefault()
    const startX = event.clientX
    const startWidth = columnWidths[column]

    function onMouseMove(moveEvent: globalThis.MouseEvent) {
      const nextWidth = Math.max(80, startWidth + moveEvent.clientX - startX)
      setColumnWidths((current) => {
        const updated = { ...current, [column]: nextWidth }
        window.localStorage.setItem(COLUMN_WIDTH_STORAGE_KEY, JSON.stringify(updated))
        return updated
      })
    }

    function onMouseUp() {
      document.body.classList.remove('is-resizing-column')
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }

    document.body.classList.add('is-resizing-column')
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
  }

  function resetColumnWidths() {
    setColumnWidths(DEFAULT_COLUMN_WIDTHS)
    window.localStorage.removeItem(COLUMN_WIDTH_STORAGE_KEY)
  }

  function changeRowPageSize(value: string) {
    setRowPageSize(value === 'all' ? 'all' : (Number(value) as RowPageSize))
    setCurrentTablePage(1)
  }

  function sortByColumn(field: EditableField) {
    if (sortField === field) {
      setSortDirection((current) => (current === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDirection('asc')
    }
    setCurrentTablePage(1)
  }

  function auditLogSummary(log: AuditLog) {
    if (log.action === 'update' && log.before && log.after) {
      const changes = BASE_COLUMNS.flatMap((column) => {
        const beforeValue = String(log.before?.[column.field] ?? '')
        const afterValue = String(log.after?.[column.field] ?? '')
        return beforeValue === afterValue ? [] : [`${column.label}: "${beforeValue}" to "${afterValue}"`]
      })
      return changes.length ? changes.join('; ') : 'Updated tracker row'
    }

    const itemName = String(log.after?.item_name ?? log.before?.item_name ?? '').trim()
    const programName = String(log.after?.program_name ?? log.before?.program_name ?? '').trim()
    const label = [programName, itemName].filter(Boolean).join(' - ')
    if (log.action === 'create') return label ? `Created ${label}` : 'Created tracker row'
    if (log.action === 'delete') return label ? `Deleted ${label}` : 'Deleted tracker row'
    return 'Tracker row changed'
  }

  if (authChecking) {
    return (
      <main className="auth-shell">
        <div className="auth-card">Checking your session...</div>
      </main>
    )
  }

  if (!currentUser) {
    return (
      <main className="auth-shell">
        <form className="auth-card" onSubmit={login}>
          <img className="auth-logo" src={smartBuyLogo} alt="SmartBuy" />
          <p className="eyebrow">Project Tracker</p>
          <h1>Sign In</h1>
          <p className="subtitle">Use your invited account to access the tracker.</p>

          <label>
            Email
            <input
              autoComplete="email"
              value={loginForm.email}
              onChange={(event) => setLoginForm((current) => ({ ...current, email: event.target.value }))}
            />
          </label>
          <label>
            Password
            <input
              autoComplete="current-password"
              type="password"
              value={loginForm.password}
              onChange={(event) =>
                setLoginForm((current) => ({ ...current, password: event.target.value }))
              }
            />
          </label>

          {authError && <div className="error">{authError}</div>}
          <button className="primary-action" type="submit">
            Log In
          </button>
          <p className="dev-note">Dev admin: admin@example.com / Admin123</p>
        </form>
      </main>
    )
  }

  const isAdmin = currentUser.role === 'admin'

  return (
    <main className="app-shell">
      <header className="brand-header">
        <div className="brand-topline">
          <img className="smartbuy-logo" src={smartBuyLogo} alt="SmartBuy" />
          <span>Analytics On Demand</span>
        </div>
        <nav className="app-nav" aria-label="Primary navigation">
          <button
            className={page === 'tracker' ? 'active' : ''}
            type="button"
            onClick={() => setPage('tracker')}
          >
            Tracker
          </button>
          {isAdmin && (
            <>
              <button
                className={page === 'admin-users' ? 'active' : ''}
                type="button"
                onClick={() => setPage('admin-users')}
              >
                Admin Users
              </button>
              <button
                className={page === 'system-log' ? 'active' : ''}
                type="button"
                onClick={() => setPage('system-log')}
              >
                System Log
              </button>
            </>
          )}
          <button type="button" onClick={handleLogout}>
            Log Out
          </button>
        </nav>
      </header>

      <section className="client-title-row">
        <div className="client-title-copy">
          <div className="client-title-divider" />
          <div>
            <p className="eyebrow">Project Tracker</p>
            <h1>
              {page === 'admin-users'
                ? 'Admin Users'
                : page === 'system-log'
                  ? 'System Log'
                  : 'Tracker Dashboard'}
            </h1>
            <p className="subtitle">
              Signed in as {currentUser.name || currentUser.email} ({currentUser.role}).
            </p>
          </div>
        </div>
        <img className="phillips-logo" src={phillipsLogo} alt="Phillips Distilling Co" />
      </section>

      {page === 'admin-users' ? (
        <section className="admin-page">
          {error && <div className="error">{error}</div>}
          {adminMessage && <div className="success">{adminMessage}</div>}

          <form className="admin-create-card" onSubmit={createUser}>
            <h2>Create User</h2>
            <label>
              Name
              <input
                value={newUser.name}
                onChange={(event) => setNewUser((current) => ({ ...current, name: event.target.value }))}
                placeholder="Jane Smith"
              />
            </label>
            <label>
              Email
              <input
                required
                type="email"
                value={newUser.email}
                onChange={(event) => setNewUser((current) => ({ ...current, email: event.target.value }))}
                placeholder="jane@example.com"
              />
            </label>
            <label>
              Temporary Password
              <div className="password-field">
                <input
                  required
                  minLength={6}
                  type={showNewUserPassword ? 'text' : 'password'}
                  value={newUser.password}
                  onChange={(event) =>
                    setNewUser((current) => ({ ...current, password: event.target.value }))
                  }
                />
                <button
                  aria-label={showNewUserPassword ? 'Hide temporary password' : 'Show temporary password'}
                  className="password-toggle"
                  type="button"
                  onClick={() => setShowNewUserPassword((current) => !current)}
                >
                  <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24">
                    <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
                    <circle cx="12" cy="12" r="3" />
                  </svg>
                </button>
              </div>
            </label>
            <label>
              Role
              <select
                value={newUser.role}
                onChange={(event) =>
                  setNewUser((current) => ({ ...current, role: event.target.value as Role }))
                }
              >
                <option value="editor">Editor</option>
                <option value="admin">Admin</option>
              </select>
            </label>
            <label className="checkbox-label">
              <input
                checked={newUser.is_active}
                type="checkbox"
                onChange={(event) =>
                  setNewUser((current) => ({ ...current, is_active: event.target.checked }))
                }
              />
              Active
            </label>
            <button className="primary-action" type="submit">
              Create User
            </button>
          </form>

          <section className="table-card admin-users-card">
            <div className="table-tools">
              <span>{usersLoading ? 'Loading users...' : `${users.length} users`}</span>
              <button type="button" onClick={loadUsers}>
                Refresh
              </button>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Email</th>
                    <th>Role</th>
                    <th>Active</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => (
                    <tr key={user.id}>
                      <td>
                        <input
                          value={user.name}
                          onBlur={(event) => updateUser(user.id, { name: event.target.value })}
                          onChange={(event) =>
                            setUsers((current) =>
                              current.map((row) =>
                                row.id === user.id ? { ...row, name: event.target.value } : row,
                              ),
                            )
                          }
                        />
                      </td>
                      <td>
                        <input
                          type="email"
                          value={user.email}
                          onBlur={(event) => updateUser(user.id, { email: event.target.value })}
                          onChange={(event) =>
                            setUsers((current) =>
                              current.map((row) =>
                                row.id === user.id ? { ...row, email: event.target.value } : row,
                              ),
                            )
                          }
                        />
                      </td>
                      <td>
                        <select
                          value={user.role}
                          onChange={(event) => updateUser(user.id, { role: event.target.value as Role })}
                        >
                          <option value="editor">Editor</option>
                          <option value="admin">Admin</option>
                        </select>
                      </td>
                      <td className="actions">
                        <input
                          checked={user.is_active}
                          type="checkbox"
                          onChange={(event) => updateUser(user.id, { is_active: event.target.checked })}
                        />
                      </td>
                      <td className="actions">
                        <button type="button" onClick={() => resetUserPassword(user.id)}>
                          Reset Password
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </section>
      ) : page === 'system-log' ? (
        <>
          {error && <div className="error">{error}</div>}
          <section className="table-card audit-log-card">
            <div className="table-tools">
              <span>{logsLoading ? 'Loading system log...' : `${auditLogs.length} log entries`}</span>
              <button type="button" onClick={loadAuditLogs}>
                Refresh
              </button>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>User</th>
                    <th>Action</th>
                    <th>Sheet</th>
                    <th>Item</th>
                    <th>Summary</th>
                  </tr>
                </thead>
                <tbody>
                  {auditLogs.map((log) => (
                    <tr key={log.id}>
                      <td>{new Date(log.created_at).toLocaleString()}</td>
                      <td>{log.user_email}</td>
                      <td>{log.action}</td>
                      <td>{log.sheet ?? ''}</td>
                      <td>{log.item_id ?? ''}</td>
                      <td>
                        <code>{auditLogSummary(log)}</code>
                      </td>
                    </tr>
                  ))}
                  {!logsLoading && auditLogs.length === 0 && (
                    <tr>
                      <td colSpan={6} className="empty-state">
                        No system log entries yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : (
        <>
          <div className="tracker-actions">
            <button className="primary-action" type="button" onClick={openAddForm}>
              Add
            </button>
            <button
              className={`submit-action ${hasPendingChanges ? 'ready' : ''}`}
              disabled={!hasPendingChanges || isSubmittingChanges}
              type="button"
              onClick={submitChanges}
            >
              {isSubmittingChanges
                ? 'Submitting...'
                : hasPendingChanges
                  ? `Submit Changes (${pendingChangeCount})`
                  : 'Submit Changes'}
            </button>
          </div>

          <section className="tabs" aria-label="Tracker sheets">
        {SHEET_ORDER.map((sheet) => (
          <button
            key={sheet}
            className={sheet === activeSheet ? 'active' : ''}
            type="button"
            onClick={() => {
              setActiveSheet(sheet)
              setStatusFilter('')
              setCurrentTablePage(1)
            }}
          >
            {sheets?.[sheet]?.label ?? sheet}
          </button>
        ))}
          </section>

          <section className="toolbar">
        <label>
          Search
          <input
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
              setCurrentTablePage(1)
            }}
            placeholder="Brand, program, item, notes, tracking..."
          />
        </label>

        <label>
          Status
          <select
            value={statusFilter}
            onChange={(event) => {
              setStatusFilter(event.target.value)
              setCurrentTablePage(1)
            }}
          >
            <option value="">All statuses</option>
            {activeStatuses.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
        </label>

        <button
          className="secondary-action"
          type="button"
          onClick={() => {
            setQuery('')
            setStatusFilter('')
            setCurrentTablePage(1)
          }}
        >
          Reset Filters
        </button>

        <div className="row-count">
          {loading ? 'Loading...' : `${items.length.toLocaleString()} rows`}
        </div>
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
                  <button type="button" onClick={() => setIsAddFormOpen(false)}>
                    Close
                  </button>
                </div>

                <div className="add-form-grid">
                  {columns.map((column) => (
                    <label key={column.field}>
                      {column.label}
                      {column.field === 'current_status' ? (
                        <select
                          value={addForm.current_status}
                          onChange={(event) =>
                            setAddForm((current) => ({
                              ...current,
                              current_status: event.target.value,
                            }))
                          }
                        >
                          <option value="">No Status</option>
                          {activeStatuses.map((status) => (
                            <option key={status} value={status}>
                              {status}
                            </option>
                          ))}
                        </select>
                      ) : column.field === 'visual_reference' ? (
                        <div className="visual-reference-field">
                          <input
                            value={addForm.visual_reference}
                            onChange={(event) =>
                              setAddForm((current) => ({
                                ...current,
                                visual_reference: event.target.value,
                              }))
                            }
                            placeholder="Paste a URL or upload an image"
                          />
                          <div className="visual-reference-actions">
                            <label className="upload-chip">
                              {uploadingVisualReference === 'add-visual-reference'
                                ? 'Uploading...'
                                : 'Upload Image'}
                              <input
                                accept="image/jpeg,image/png,image/webp"
                                disabled={uploadingVisualReference === 'add-visual-reference'}
                                type="file"
                                onChange={(event) => {
                                  const file = event.target.files?.[0]
                                  event.target.value = ''
                                  if (!file) return
                                  uploadVisualReference(file, 'add-visual-reference', (url) =>
                                    setAddForm((current) => ({ ...current, visual_reference: url })),
                                  )
                                }}
                              />
                            </label>
                            {addForm.visual_reference && (
                              <a href={addForm.visual_reference} target="_blank" rel="noreferrer">
                                Preview
                              </a>
                            )}
                          </div>
                        </div>
                      ) : column.field === 'important_notes' || column.field === 'tracking' ? (
                        <textarea
                          value={addForm[column.field]}
                          onChange={(event) =>
                            setAddForm((current) => ({
                              ...current,
                              [column.field]: event.target.value,
                            }))
                          }
                        />
                      ) : (
                        <input
                          type={
                            column.field === 'date_or_buy' && activeSheet === 'ad-hoc'
                              ? 'date'
                              : 'text'
                          }
                          value={addForm[column.field]}
                          onChange={(event) =>
                            setAddForm((current) => ({
                              ...current,
                              [column.field]: event.target.value,
                            }))
                          }
                        />
                      )}
                    </label>
                  ))}
                </div>

                <div className="modal-actions">
                  <button type="button" onClick={() => setIsAddFormOpen(false)}>
                    Cancel
                  </button>
                  <button className="primary-action" type="submit">
                    Add Draft
                  </button>
                </div>
              </form>
            </div>
          )}

          <section className="status-summary" aria-label="Status summary">
        {Object.entries(statusCounts).map(([status, count]) => (
          <button
            key={status}
            type="button"
            onClick={() => {
              setStatusFilter(status === 'No Status' ? '' : status)
              setCurrentTablePage(1)
            }}
          >
            <strong>{count}</strong>
            <span>{status}</span>
          </button>
        ))}
          </section>

          <section className="table-card">
        <div className="table-tools">
          <span>Drag the right edge of any column header to resize it.</span>
          <div className="table-tool-actions">
            <label>
              Rows
              <select value={rowPageSize} onChange={(event) => changeRowPageSize(event.target.value)}>
                <option value={10}>10</option>
                <option value={20}>20</option>
                <option value={30}>30</option>
                <option value="all">All</option>
              </select>
            </label>
            <button type="button" onClick={resetColumnWidths}>
              Reset Columns
            </button>
          </div>
        </div>
        <div className="table-scroll">
          <table style={{ minWidth: tableWidth }}>
            <colgroup>
              {columns.map((column) => (
                <col key={column.field} style={{ width: columnWidths[column.field] }} />
              ))}
              <col style={{ width: columnWidths.actions }} />
            </colgroup>
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column.field} className={column.className}>
                    <button
                      aria-label={`Sort by ${column.label}`}
                      aria-sort={
                        sortField === column.field
                          ? sortDirection === 'asc'
                            ? 'ascending'
                            : 'descending'
                          : 'none'
                      }
                      className="column-sort"
                      type="button"
                      onClick={() => sortByColumn(column.field)}
                    >
                      <span>{column.label}</span>
                      <span className="sort-arrows" aria-hidden="true">
                        <span className={sortField === column.field && sortDirection === 'asc' ? 'active' : ''}>
                          ▲
                        </span>
                        <span className={sortField === column.field && sortDirection === 'desc' ? 'active' : ''}>
                          ▼
                        </span>
                      </span>
                    </button>
                    <button
                      aria-label={`Resize ${column.label} column`}
                      className="column-resizer"
                      type="button"
                      onMouseDown={(event) => resizeColumn(column.field, event)}
                    />
                  </th>
                ))}
                <th className="actions">
                  <span>Actions</span>
                  <button
                    aria-label="Resize Actions column"
                    className="column-resizer"
                    type="button"
                    onMouseDown={(event) => resizeColumn('actions', event)}
                  />
                </th>
              </tr>
            </thead>
            <tbody>
              {!loading &&
                visibleItems.map((item) => (
                  <tr key={item.id}>
                    {columns.map((column) => {
                      const value = item[column.field] ?? ''

                      return (
                        <td key={column.field} className={column.className}>
                          {column.field === 'current_status' ? (
                            <select
                              value={value}
                              onChange={(event) => {
                                updateDraft(item.id, column.field, event.target.value)
                              }}
                            >
                              <option value="">No Status</option>
                              {activeStatuses.map((status) => (
                                <option key={status} value={status}>
                                  {status}
                                </option>
                              ))}
                            </select>
                          ) : column.field === 'visual_reference' ? (
                            <div className="visual-reference-field">
                              <input
                                value={value}
                                onChange={(event) =>
                                  updateDraft(item.id, column.field, event.target.value)
                                }
                              />
                              <div className="visual-reference-actions">
                                <label className="upload-chip">
                                  {uploadingVisualReference === `${item.id}-visual-reference`
                                    ? 'Uploading...'
                                    : 'Upload'}
                                  <input
                                    accept="image/jpeg,image/png,image/webp"
                                    disabled={uploadingVisualReference === `${item.id}-visual-reference`}
                                    type="file"
                                    onChange={(event) => {
                                      const file = event.target.files?.[0]
                                      event.target.value = ''
                                      if (!file) return
                                      uploadVisualReference(file, `${item.id}-visual-reference`, (url) =>
                                        updateDraft(item.id, column.field, url),
                                      )
                                    }}
                                  />
                                </label>
                                {value && (
                                  <a href={value} target="_blank" rel="noreferrer">
                                    Preview
                                  </a>
                                )}
                              </div>
                            </div>
                          ) : column.field === 'important_notes' || column.field === 'tracking' ? (
                            <textarea
                              value={value}
                              onChange={(event) =>
                                updateDraft(item.id, column.field, event.target.value)
                              }
                            />
                          ) : (
                            <input
                              type={
                                column.field === 'date_or_buy' && activeSheet === 'ad-hoc'
                                  ? 'date'
                                  : 'text'
                              }
                              value={value}
                              onChange={(event) =>
                                updateDraft(item.id, column.field, event.target.value)
                              }
                            />
                          )}
                          {item.id < 0 && <span className="saving">New draft</span>}
                          {item.id > 0 && pendingUpdates[item.id]?.[column.field] !== undefined && (
                            <span className="saving">Unsaved</span>
                          )}
                        </td>
                      )
                    })}
                    <td className="actions">
                      {isAdmin ? (
                        <button type="button" onClick={() => deleteRow(item.id)}>
                          Delete
                        </button>
                      ) : (
                        <span className="muted">Admin only</span>
                      )}
                    </td>
                  </tr>
                ))}
              {!loading && items.length === 0 && (
                <tr>
                  <td colSpan={columns.length + 1} className="empty-state">
                    No rows match the current filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {!loading && items.length > 0 && (
          <div className="table-pagination">
            <span>
              Showing {visibleStart.toLocaleString()}-{visibleEnd.toLocaleString()} of{' '}
              {items.length.toLocaleString()} rows
            </span>
            {rowPageSize !== 'all' && (
              <div className="pagination-actions">
                <button
                  disabled={activeTablePage === 1}
                  type="button"
                  onClick={() => setCurrentTablePage((page) => Math.max(1, page - 1))}
                >
                  Previous
                </button>
                <span>
                  Page {activeTablePage} of {tablePageCount}
                </span>
                <button
                  disabled={activeTablePage === tablePageCount}
                  type="button"
                  onClick={() => setCurrentTablePage((page) => Math.min(tablePageCount, page + 1))}
                >
                  Next
                </button>
              </div>
            )}
          </div>
        )}
          </section>
        </>
      )}
    </main>
  )
}

export default App
