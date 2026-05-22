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
type StatusTargetSheet = SheetKey | 'all'
type Role = 'admin' | 'editor'
type Page = 'tracker' | 'admin-users' | 'system-log'
type AdminSettingsTab = 'users' | 'statuses' | 'kanban'
type RowPageSize = 10 | 20 | 30 | 'all'
type SortDirection = 'asc' | 'desc'
type ViewerStatus = 'on-track' | 'not-on-track'

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

type KanbanColumn = {
  id: number
  title: string
  statuses: string[]
  sort_order: number
  is_visible: boolean
  created_at: string
  updated_at: string
}

type SheetStatus = {
  id: number
  sheet: SheetKey
  status: string
  sort_order: number
  created_at: string
  updated_at: string
}

type SurveyItem = {
  id: number
  item_name: string
  brand: string
  channel: string
  item_description: string
  uom: string
  price: string
  image_url: string
  sort_order: number
  is_active: boolean
  created_at: string
  updated_at: string
}

type SurveyResponseDraft = {
  email: string
  attention_effectiveness: number
  recommend_rollout: 'Yes' | 'No' | 'Maybe'
  retail_engagement: number
  stands_out: 'Yes' | 'No' | 'Neutral'
  price_reasonable: 'Yes' | 'No'
  feedback: string
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
const PUBLIC_SHEET_ORDER: SheetKey[] = ['ad-hoc', 'buys']
const COLUMN_WIDTH_STORAGE_KEY = 'phillips-tracker-column-widths'
const NOT_ON_TRACK_STATUSES = new Set([
  'Details Needed',
  'Quoting',
  'Pending Feedback',
  'ON HOLD',
  'Canceled',
])

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

const EMPTY_SURVEY_RESPONSE: SurveyResponseDraft = {
  email: '',
  attention_effectiveness: 5,
  recommend_rollout: 'Yes',
  retail_engagement: 5,
  stands_out: 'Yes',
  price_reasonable: 'Yes',
  feedback: '',
}

function App() {
  const [currentPath, setCurrentPath] = useState(window.location.pathname)
  const [token, setToken] = useState(() => window.localStorage.getItem(AUTH_STORAGE_KEY) ?? '')
  const [currentUser, setCurrentUser] = useState<AppUser | null>(null)
  const [page, setPage] = useState<Page>('tracker')
  const [adminSettingsTab, setAdminSettingsTab] = useState<AdminSettingsTab>('users')
  const [authChecking, setAuthChecking] = useState(Boolean(token))
  const [sheets, setSheets] = useState<Record<SheetKey, SheetMeta> | null>(null)
  const [activeSheet, setActiveSheet] = useState<SheetKey>('ad-hoc')
  const [items, setItems] = useState<TrackerItem[]>([])
  const [users, setUsers] = useState<AppUser[]>([])
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([])
  const [kanbanColumns, setKanbanColumns] = useState<KanbanColumn[]>([])
  const [sheetStatuses, setSheetStatuses] = useState<SheetStatus[]>([])
  const [query, setQuery] = useState('')
  const [publicQuery, setPublicQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [publicItems, setPublicItems] = useState<TrackerItem[]>([])
  const [surveyItems, setSurveyItems] = useState<SurveyItem[]>([])
  const [rowPageSize, setRowPageSize] = useState<RowPageSize>(20)
  const [currentTablePage, setCurrentTablePage] = useState(1)
  const [sortField, setSortField] = useState<EditableField>('date_or_buy')
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc')
  const [pendingCreates, setPendingCreates] = useState<TrackerItem[]>([])
  const [pendingUpdates, setPendingUpdates] = useState<PendingUpdates>({})
  const [pendingDeletes, setPendingDeletes] = useState<number[]>([])
  const [isAddFormOpen, setIsAddFormOpen] = useState(false)
  const [isSurveyFormOpen, setIsSurveyFormOpen] = useState(false)
  const [addForm, setAddForm] = useState<ItemDraft>(EMPTY_ITEM_DRAFT)
  const [refreshItemsToken, setRefreshItemsToken] = useState(0)
  const [loading, setLoading] = useState(true)
  const [publicLoading, setPublicLoading] = useState(false)
  const [surveyLoading, setSurveyLoading] = useState(false)
  const [usersLoading, setUsersLoading] = useState(false)
  const [logsLoading, setLogsLoading] = useState(false)
  const [kanbanColumnsLoading, setKanbanColumnsLoading] = useState(false)
  const [statusesLoading, setStatusesLoading] = useState(false)
  const [isSubmittingChanges, setIsSubmittingChanges] = useState(false)
  const [isSubmittingSurvey, setIsSubmittingSurvey] = useState(false)
  const [uploadingVisualReference, setUploadingVisualReference] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [publicError, setPublicError] = useState('')
  const [surveyError, setSurveyError] = useState('')
  const [authError, setAuthError] = useState('')
  const [adminMessage, setAdminMessage] = useState('')
  const [kanbanMessage, setKanbanMessage] = useState('')
  const [surveyMessage, setSurveyMessage] = useState('')
  const [surveyQuery, setSurveyQuery] = useState('')
  const [selectedSurveyItemIds, setSelectedSurveyItemIds] = useState<number[]>([])
  const [surveyForm, setSurveyForm] = useState<SurveyResponseDraft>(EMPTY_SURVEY_RESPONSE)
  const [loginForm, setLoginForm] = useState({ email: 'admin@example.com', password: 'Admin123' })
  const [showNewUserPassword, setShowNewUserPassword] = useState(false)
  const [newUser, setNewUser] = useState({
    email: '',
    name: '',
    password: '',
    role: 'editor' as Role,
    is_active: true,
  })
  const [newKanbanColumn, setNewKanbanColumn] = useState({
    title: '',
    statuses: [] as string[],
    sort_order: 0,
    is_visible: true,
  })
  const [newSheetStatus, setNewSheetStatus] = useState({
    sheet: 'ad-hoc' as StatusTargetSheet,
    status: '',
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

  const isPublicViewerPath = currentPath === '/viewer' || currentPath === '/viewer/kanban'
  const isPublicKanbanPath = currentPath === '/viewer/kanban'
  const isPublicSurveyPath = currentPath === '/survey'

  useEffect(() => {
    function handlePopState() {
      setCurrentPath(window.location.pathname)
    }

    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  const handleLogout = useCallback(() => {
    window.localStorage.removeItem(AUTH_STORAGE_KEY)
    setToken('')
    setCurrentUser(null)
    setSheets(null)
    setItems([])
    setUsers([])
    setAuditLogs([])
    setKanbanColumns([])
    setSheetStatuses([])
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
    if (!isPublicViewerPath) return

    const controller = new AbortController()

    async function loadPublicItems() {
      setPublicLoading(true)
      setPublicError('')
      const params = new URLSearchParams()
      if (publicQuery.trim()) params.set('q', publicQuery.trim())

      try {
        const response = await fetch(`${API_BASE}/public/items?${params}`, {
          signal: controller.signal,
        })
        if (!response.ok) throw new Error('Unable to load viewer data.')
        const data = await response.json()
        setPublicItems(data.items)
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') return
        setPublicError(err instanceof Error ? err.message : 'Unable to load viewer data.')
      } finally {
        setPublicLoading(false)
      }
    }

    loadPublicItems()
    return () => controller.abort()
  }, [isPublicViewerPath, publicQuery])

  useEffect(() => {
    if (!isPublicSurveyPath) return

    const controller = new AbortController()

    async function loadSurveyItems() {
      setSurveyLoading(true)
      setSurveyError('')
      const params = new URLSearchParams()
      if (surveyQuery.trim()) params.set('q', surveyQuery.trim())

      try {
        const response = await fetch(`${API_BASE}/public/survey-items?${params}`, {
          signal: controller.signal,
        })
        if (!response.ok) throw new Error('Unable to load survey items.')
        const data = await response.json()
        setSurveyItems(data.items)
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') return
        setSurveyError(err instanceof Error ? err.message : 'Unable to load survey items.')
      } finally {
        setSurveyLoading(false)
      }
    }

    loadSurveyItems()
    return () => controller.abort()
  }, [isPublicSurveyPath, surveyQuery])

  const loadSheets = useCallback(async () => {
    const response = await apiFetch('/sheets')
    if (!response.ok) throw new Error('Unable to load sheet configuration.')
    const data = await response.json()
    setSheets(data.sheets)
  }, [apiFetch])

  useEffect(() => {
    if (!currentUser) return

    const timeoutId = window.setTimeout(() => {
      loadSheets().catch((err: Error) => setError(err.message))
    }, 0)

    return () => window.clearTimeout(timeoutId)
  }, [currentUser, loadSheets])

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
    if (!currentUser || currentUser.role !== 'admin' || page !== 'admin-users' || adminSettingsTab !== 'users') {
      return
    }

    const timeoutId = window.setTimeout(() => {
      loadUsers()
    }, 0)

    return () => window.clearTimeout(timeoutId)
  }, [adminSettingsTab, currentUser, loadUsers, page])

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

  const loadKanbanColumns = useCallback(async (admin = false) => {
    setKanbanColumnsLoading(true)
    if (admin) {
      setError('')
    } else {
      setPublicError('')
    }

    try {
      const response = admin
        ? await apiFetch('/admin/kanban-columns')
        : await fetch(`${API_BASE}/public/kanban-columns`)
      if (!response.ok) throw new Error('Unable to load kanban columns.')
      const data = await response.json()
      setKanbanColumns(data.columns)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unable to load kanban columns.'
      if (admin) {
        setError(message)
      } else {
        setPublicError(message)
      }
    } finally {
      setKanbanColumnsLoading(false)
    }
  }, [apiFetch])

  const loadSheetStatuses = useCallback(async () => {
    setStatusesLoading(true)
    setError('')
    try {
      const response = await apiFetch('/admin/sheet-statuses')
      if (!response.ok) throw new Error('Unable to load tracker statuses.')
      const data = await response.json()
      setSheetStatuses(data.statuses)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load tracker statuses.')
    } finally {
      setStatusesLoading(false)
    }
  }, [apiFetch])

  useEffect(() => {
    if (!isPublicViewerPath) return

    const timeoutId = window.setTimeout(() => {
      loadKanbanColumns(false)
    }, 0)

    return () => window.clearTimeout(timeoutId)
  }, [isPublicViewerPath, loadKanbanColumns])

  useEffect(() => {
    if (!currentUser || currentUser.role !== 'admin' || page !== 'admin-users' || adminSettingsTab !== 'kanban') {
      return
    }

    const timeoutId = window.setTimeout(() => {
      loadKanbanColumns(true)
      loadSheetStatuses()
    }, 0)

    return () => window.clearTimeout(timeoutId)
  }, [adminSettingsTab, currentUser, loadKanbanColumns, loadSheetStatuses, page])

  useEffect(() => {
    if (
      !currentUser ||
      currentUser.role !== 'admin' ||
      page !== 'admin-users' ||
      adminSettingsTab !== 'statuses'
    ) {
      return
    }

    const timeoutId = window.setTimeout(() => {
      loadSheetStatuses()
    }, 0)

    return () => window.clearTimeout(timeoutId)
  }, [adminSettingsTab, currentUser, loadSheetStatuses, page])

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

  async function createSheetStatus(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAdminMessage('')
    setError('')

    const targetSheets = newSheetStatus.sheet === 'all' ? SHEET_ORDER : [newSheetStatus.sheet]
    const createdStatuses: SheetStatus[] = []
    const skippedSheets: string[] = []

    for (const sheetKey of targetSheets) {
      const response = await apiFetch('/admin/sheet-statuses', {
        method: 'POST',
        body: JSON.stringify({ sheet: sheetKey, status: newSheetStatus.status }),
      })
      if (response.status === 409) {
        skippedSheets.push(sheets?.[sheetKey]?.label ?? sheetKey)
        continue
      }
      if (!response.ok) {
        setError('Unable to create status.')
        return
      }
      const data = await response.json()
      createdStatuses.push(data.status)
    }

    if (createdStatuses.length > 0) {
      setSheetStatuses((current) => [...current, ...createdStatuses])
    }
    setNewSheetStatus((current) => ({ ...current, status: '' }))
    await loadSheets()

    const createdMessage =
      createdStatuses.length === 1 ? 'Status created.' : `Status created for ${createdStatuses.length} sheets.`
    setAdminMessage(
      skippedSheets.length
        ? `${createdMessage} Already existed for: ${skippedSheets.join(', ')}.`
        : createdMessage,
    )
  }

  async function deleteSheetStatus(statusRow: SheetStatus) {
    const confirmed = window.confirm(`Remove "${statusRow.status}" from ${sheets?.[statusRow.sheet]?.label ?? 'this sheet'}?`)
    if (!confirmed) return
    setAdminMessage('')
    setError('')
    const response = await apiFetch(`/admin/sheet-statuses/${statusRow.id}`, { method: 'DELETE' })
    if (!response.ok) {
      setError('Unable to remove status.')
      return
    }
    setSheetStatuses((current) => current.filter((row) => row.id !== statusRow.id))
    await loadSheets()
    setAdminMessage('Status removed.')
  }

  function toggleStatusSelection(selectedStatuses: string[], statusLabel: string) {
    return selectedStatuses.includes(statusLabel)
      ? selectedStatuses.filter((status) => status !== statusLabel)
      : [...selectedStatuses, statusLabel]
  }

  async function createKanbanColumn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setKanbanMessage('')
    setError('')
    const response = await apiFetch('/admin/kanban-columns', {
      method: 'POST',
      body: JSON.stringify({
        title: newKanbanColumn.title,
        statuses: newKanbanColumn.statuses,
        sort_order: newKanbanColumn.sort_order,
        is_visible: newKanbanColumn.is_visible,
      }),
    })
    if (!response.ok) {
      setError('Unable to create kanban column.')
      return
    }
    const data = await response.json()
    setKanbanColumns((current) => [...current, data.column])
    setNewKanbanColumn({ title: '', statuses: [], sort_order: 0, is_visible: true })
    setKanbanMessage('Kanban column created.')
  }

  async function updateKanbanColumn(columnId: number, updates: Partial<KanbanColumn>) {
    setKanbanMessage('')
    setError('')
    const response = await apiFetch(`/admin/kanban-columns/${columnId}`, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    })
    if (!response.ok) {
      setError('Unable to update kanban column.')
      return
    }
    const data = await response.json()
    setKanbanColumns((current) =>
      current.map((column) => (column.id === columnId ? data.column : column)),
    )
    setKanbanMessage('Kanban column updated.')
  }

  async function deleteKanbanColumn(columnId: number) {
    const confirmed = window.confirm('Delete this kanban column? Items will not be deleted.')
    if (!confirmed) return
    setKanbanMessage('')
    setError('')
    const response = await apiFetch(`/admin/kanban-columns/${columnId}`, { method: 'DELETE' })
    if (!response.ok) {
      setError('Unable to delete kanban column.')
      return
    }
    setKanbanColumns((current) => current.filter((column) => column.id !== columnId))
    setKanbanMessage('Kanban column deleted.')
  }

  const activeStatuses = sheets?.[activeSheet]?.statuses ?? []
  const allStatusOptions = useMemo(() => {
    const statuses = new Set<string>()
    if (sheets) {
      SHEET_ORDER.forEach((sheetKey) => {
        sheets[sheetKey]?.statuses.forEach((statusLabel) => statuses.add(statusLabel))
      })
    }
    sheetStatuses.forEach((statusRow) => statuses.add(statusRow.status))
    return [...statuses].sort((first, second) => first.localeCompare(second))
  }, [sheets, sheetStatuses])
  const selectedSurveyItems = useMemo(() => {
    return surveyItems.filter((item) => selectedSurveyItemIds.includes(item.id))
  }, [selectedSurveyItemIds, surveyItems])
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

  const publicCounts = useMemo(() => {
    return publicItems.reduce(
      (counts, item) => {
        counts[getViewerStatus(item)] += 1
        return counts
      },
      { 'on-track': 0, 'not-on-track': 0 } as Record<ViewerStatus, number>,
    )
  }, [publicItems])

  const publicItemsBySheet = useMemo(() => {
    return PUBLIC_SHEET_ORDER.reduce(
      (groups, sheet) => {
        groups[sheet] = publicItems.filter((item) => item.sheet === sheet)
        return groups
      },
      {} as Record<SheetKey, TrackerItem[]>,
    )
  }, [publicItems])

  const visibleKanbanColumns = useMemo(() => {
    return [...kanbanColumns]
      .filter((column) => column.is_visible)
      .sort((first, second) => first.sort_order - second.sort_order || first.id - second.id)
  }, [kanbanColumns])

  const publicKanbanColumns = useMemo(() => {
    const columnsWithItems = visibleKanbanColumns.map((column) => ({
      column,
      items: publicItems.filter((item) => column.statuses.includes(item.current_status)),
    }))
    const matchedStatuses = new Set(visibleKanbanColumns.flatMap((column) => column.statuses))
    const unmatchedItems = publicItems.filter((item) => !matchedStatuses.has(item.current_status))
    if (unmatchedItems.length > 0) {
      columnsWithItems.push({
        column: {
          id: -1,
          title: 'Other',
          statuses: [],
          sort_order: 999,
          is_visible: true,
          created_at: '',
          updated_at: '',
        },
        items: unmatchedItems,
      })
    }
    return columnsWithItems
  }, [publicItems, visibleKanbanColumns])

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

  function isImageReference(value: string) {
    if (!value.trim()) return false

    try {
      const url = new URL(value)
      return (
        url.hostname.includes('res.cloudinary.com') ||
        /\.(avif|gif|jpe?g|png|webp)$/i.test(url.pathname)
      )
    } catch {
      return false
    }
  }

  function getViewerStatus(item: TrackerItem): ViewerStatus {
    return NOT_ON_TRACK_STATUSES.has(item.current_status) ? 'not-on-track' : 'on-track'
  }

  function viewerStatusLabel(status: ViewerStatus) {
    return status === 'on-track' ? 'On Track' : 'Not On Track'
  }

  function publicSheetLabel(sheet: SheetKey) {
    if (sheet === 'ad-hoc') return 'Ad Hoc'
    if (sheet === 'buys') return 'Buys'
    return 'Completed'
  }

  function goToPath(path: string) {
    window.history.pushState({}, '', path)
    setCurrentPath(path)
  }

  function publicCard(item: TrackerItem) {
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

  function renderStatusMultiSelect(
    selectedStatuses: string[],
    onChange: (nextStatuses: string[]) => void,
  ) {
    return (
      <details className="status-multi-select">
        <summary>
          <span>{selectedStatuses.length ? `${selectedStatuses.length} selected` : 'Choose statuses'}</span>
        </summary>
        <div className="status-multi-menu">
          {allStatusOptions.map((statusLabel) => (
            <label key={statusLabel} className="status-option">
              <input
                checked={selectedStatuses.includes(statusLabel)}
                type="checkbox"
                onChange={() => onChange(toggleStatusSelection(selectedStatuses, statusLabel))}
              />
              {statusLabel}
            </label>
          ))}
          {allStatusOptions.length === 0 && <span className="muted">No statuses available yet.</span>}
        </div>
        {selectedStatuses.length > 0 && (
          <div className="selected-status-chips">
            {selectedStatuses.map((statusLabel) => (
              <span key={statusLabel}>{statusLabel}</span>
            ))}
          </div>
        )}
      </details>
    )
  }

  function openSurveyResponse(item: SurveyItem) {
    setSelectedSurveyItemIds([item.id])
    setIsSurveyFormOpen(true)
    setSurveyForm(EMPTY_SURVEY_RESPONSE)
    setSurveyMessage('')
    setSurveyError('')
  }

  function openSelectedSurveyResponse() {
    if (selectedSurveyItemIds.length === 0) return
    setIsSurveyFormOpen(true)
    setSurveyForm(EMPTY_SURVEY_RESPONSE)
    setSurveyMessage('')
    setSurveyError('')
  }

  function closeSurveyResponse() {
    setIsSurveyFormOpen(false)
    setSelectedSurveyItemIds([])
    setSurveyForm(EMPTY_SURVEY_RESPONSE)
  }

  function toggleSurveyItem(itemId: number) {
    setSelectedSurveyItemIds((current) =>
      current.includes(itemId) ? current.filter((id) => id !== itemId) : [...current, itemId],
    )
  }

  function toggleAllSurveyItems() {
    setSelectedSurveyItemIds((current) =>
      current.length === surveyItems.length ? [] : surveyItems.map((item) => item.id),
    )
  }

  function escapePrintValue(value: string) {
    return value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')
  }

  function printSurveyBuyBook() {
    if (selectedSurveyItems.length === 0) return

    const printFrame = document.createElement('iframe')
    printFrame.title = 'SmartBuy Survey Buy Book'
    printFrame.style.position = 'fixed'
    printFrame.style.right = '0'
    printFrame.style.bottom = '0'
    printFrame.style.width = '0'
    printFrame.style.height = '0'
    printFrame.style.border = '0'
    document.body.appendChild(printFrame)

    const printFrameWindow = printFrame.contentWindow
    const printDocument = printFrameWindow?.document
    if (!printFrameWindow || !printDocument) {
      printFrame.remove()
      setSurveyError('Unable to prepare the print view.')
      return
    }

    const generatedDate = new Date().toLocaleDateString()
    const itemMarkup = selectedSurveyItems
      .map((item, index) => {
        const imageMarkup = isImageReference(item.image_url)
          ? `<img src="${escapePrintValue(item.image_url)}" alt="" />`
          : `<div class="image-placeholder">${escapePrintValue(item.brand.slice(0, 2).toUpperCase() || 'SB')}</div>`

        return `
          <article class="buy-book-item">
            <div class="item-number">${index + 1}</div>
            <div class="item-image">${imageMarkup}</div>
            <div class="item-copy">
              <p class="meta">${escapePrintValue(item.channel || 'No channel')} | ${escapePrintValue(item.brand || 'No brand')}</p>
              <h2>${escapePrintValue(item.item_name)}</h2>
              <p>${escapePrintValue(item.item_description || 'No description provided.')}</p>
              <dl>
                <div><dt>UOM</dt><dd>${escapePrintValue(item.uom || '-')}</dd></div>
                <div><dt>Price</dt><dd>${escapePrintValue(item.price || '-')}</dd></div>
              </dl>
            </div>
            <div class="decision-box">
              <strong>Decision</strong>
              <label><span></span> Interested</label>
              <label><span></span> Maybe</label>
              <label><span></span> Pass</label>
              <div class="notes">
                <strong>Notes / Qty</strong>
                <div></div>
                <div></div>
                <div></div>
              </div>
            </div>
          </article>
        `
      })
      .join('')

    const buyBookHtml = `
      <!doctype html>
      <html>
        <head>
          <title>SmartBuy Survey Buy Book</title>
          <style>
            * { box-sizing: border-box; }
            body {
              margin: 0;
              padding: 32px;
              color: #111;
              font-family: Arial, Helvetica, sans-serif;
              background: #fff;
            }
            header {
              display: flex;
              align-items: center;
              justify-content: space-between;
              gap: 24px;
              border-bottom: 3px solid #FD4338;
              padding-bottom: 18px;
              margin-bottom: 22px;
            }
            .brand {
              display: flex;
              align-items: center;
              gap: 14px;
            }
            .brand img {
              max-height: 42px;
              max-width: 150px;
              object-fit: contain;
            }
            h1 {
              margin: 0;
              font-size: 28px;
              letter-spacing: -0.03em;
            }
            .subhead {
              margin: 6px 0 0;
              color: #565656;
              font-size: 13px;
              font-weight: 700;
              text-transform: uppercase;
              letter-spacing: 0.08em;
            }
            .buy-book-item {
              break-inside: avoid;
              display: grid;
              grid-template-columns: 36px 120px minmax(0, 1fr) 190px;
              gap: 16px;
              align-items: start;
              border: 1px solid #d9d9d9;
              border-radius: 14px;
              padding: 14px;
              margin-bottom: 14px;
            }
            .item-number {
              width: 28px;
              height: 28px;
              display: grid;
              place-items: center;
              border-radius: 999px;
              background: #4B78FF;
              color: #fff;
              font-weight: 800;
            }
            .item-image {
              min-height: 96px;
              display: grid;
              place-items: center;
              border: 1px solid #d9d9d9;
              border-radius: 10px;
              background: #f7f7f7;
            }
            .item-image img {
              width: 100%;
              height: 96px;
              object-fit: contain;
            }
            .image-placeholder {
              width: 52px;
              height: 52px;
              display: grid;
              place-items: center;
              border-radius: 999px;
              background: #fff;
              color: #FD4338;
              font-size: 18px;
              font-weight: 800;
            }
            .meta {
              margin: 0 0 6px;
              color: #565656;
              font-size: 11px;
              font-weight: 800;
              text-transform: uppercase;
              letter-spacing: 0.08em;
            }
            h2 {
              margin: 0 0 8px;
              font-size: 19px;
            }
            .item-copy p:not(.meta) {
              margin: 0 0 12px;
              line-height: 1.4;
            }
            dl {
              display: grid;
              grid-template-columns: repeat(2, minmax(0, 1fr));
              gap: 8px;
              margin: 0;
            }
            dl div {
              border-radius: 8px;
              padding: 8px;
              background: #f4f4f4;
            }
            dt {
              color: #565656;
              font-size: 10px;
              font-weight: 800;
              text-transform: uppercase;
              letter-spacing: 0.08em;
            }
            dd {
              margin: 4px 0 0;
              font-weight: 800;
            }
            .decision-box {
              display: grid;
              gap: 9px;
              border-left: 3px solid #FD4338;
              padding-left: 12px;
            }
            .decision-box label {
              display: flex;
              align-items: center;
              gap: 8px;
              font-size: 13px;
              font-weight: 700;
            }
            .decision-box label span {
              width: 14px;
              height: 14px;
              border: 1px solid #111;
              border-radius: 3px;
            }
            .notes {
              display: grid;
              gap: 9px;
              margin-top: 6px;
            }
            .notes div {
              height: 18px;
              border-bottom: 1px solid #999;
            }
            @media print {
              body { padding: 20px; }
              .buy-book-item { page-break-inside: avoid; }
            }
          </style>
        </head>
        <body>
          <header>
            <div>
              <p class="subhead">SmartBuy Survey</p>
              <h1>Buy Book</h1>
              <p class="subhead">${selectedSurveyItems.length} selected items | Generated ${generatedDate}</p>
            </div>
            <div class="brand">
              <img src="${smartBuyLogo}" alt="SmartBuy" />
              <img src="${phillipsLogo}" alt="Phillips" />
            </div>
          </header>
          ${itemMarkup}
        </body>
      </html>
    `

    printDocument.open()
    printDocument.write(buyBookHtml)
    printDocument.close()

    window.setTimeout(() => {
      printFrameWindow.focus()
      printFrameWindow.print()
      window.setTimeout(() => printFrame.remove(), 1000)
    }, 250)
  }

  async function submitSurveyResponse(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (selectedSurveyItems.length === 0) return

    setIsSubmittingSurvey(true)
    setSurveyError('')
    setSurveyMessage('')
    try {
      for (const item of selectedSurveyItems) {
        const response = await fetch(`${API_BASE}/public/survey-responses`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            survey_item_id: item.id,
            ...surveyForm,
          }),
        })
        if (!response.ok) throw new Error('Unable to submit survey response.')
      }
      setSurveyMessage(
        selectedSurveyItems.length === 1
          ? `Thanks. Your response for ${selectedSurveyItems[0].item_name} was submitted.`
          : `Thanks. Your response was submitted for ${selectedSurveyItems.length} items.`,
      )
      closeSurveyResponse()
    } catch (err) {
      setSurveyError(err instanceof Error ? err.message : 'Unable to submit survey response.')
    } finally {
      setIsSubmittingSurvey(false)
    }
  }

  function ratingOptions(
    field: 'attention_effectiveness' | 'retail_engagement',
    scaleLabel: string,
  ) {
    return (
      <div className="survey-radio-stack">
        {[1, 2, 3, 4, 5].map((rating) => (
          <label key={rating}>
            <input
              checked={surveyForm[field] === rating}
              name={field}
              type="radio"
              value={rating}
              onChange={() => setSurveyForm((current) => ({ ...current, [field]: rating }))}
            />
            {rating}
          </label>
        ))}
        <span>{scaleLabel}</span>
      </div>
    )
  }

  if (isPublicSurveyPath) {
    return (
      <main className="app-shell viewer-shell survey-shell">
        <header className="brand-header">
          <div className="brand-topline">
            <img className="smartbuy-logo" src={smartBuyLogo} alt="SmartBuy" />
            <span>Public Survey</span>
          </div>
          <nav className="app-nav" aria-label="Survey navigation">
            <button className="active" type="button" onClick={() => goToPath('/survey')}>
              Survey
            </button>
            <button type="button" onClick={() => goToPath('/viewer')}>
              Viewer
            </button>
            <button type="button" onClick={() => goToPath('/')}>
              Sign In
            </button>
          </nav>
        </header>

        <section className="client-title-row viewer-title-row">
          <div className="client-title-copy">
            <div className="client-title-divider" />
            <div>
              <p className="eyebrow">SmartBuy Survey</p>
              <h1>Product Interest Survey</h1>
              <p className="subtitle">
                Review potential items and submit feedback on what may generate customer interest.
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
            <button
              className="secondary-action"
              type="button"
              onClick={toggleAllSurveyItems}
              disabled={surveyItems.length === 0}
            >
              {selectedSurveyItemIds.length === surveyItems.length && surveyItems.length > 0
                ? 'Clear Selection'
                : 'Select All'}
            </button>
            <button
              className="primary-action"
              type="button"
              onClick={openSelectedSurveyResponse}
              disabled={selectedSurveyItemIds.length === 0}
            >
              Review Selected ({selectedSurveyItemIds.length})
            </button>
            <button
              className="secondary-action"
              type="button"
              onClick={printSurveyBuyBook}
              disabled={selectedSurveyItemIds.length === 0}
            >
              Print Buy Book ({selectedSurveyItemIds.length})
            </button>
            <span>{surveyLoading ? 'Loading...' : `${surveyItems.length.toLocaleString()} items`}</span>
          </div>
        </section>

        {surveyError && <div className="error">{surveyError}</div>}
        {surveyMessage && <div className="success">{surveyMessage}</div>}

        <section className="survey-grid" aria-label="Survey items">
          {surveyItems.map((item) => (
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
                <p className="viewer-card-topline">
                  <span>{item.channel || 'No channel listed'}</span>
                  <strong>{item.brand || 'No brand'}</strong>
                </p>
                <h2>{item.item_name}</h2>
                <p>{item.item_description || 'No description provided yet.'}</p>
                <dl>
                  <div>
                    <dt>UOM</dt>
                    <dd>{item.uom || '-'}</dd>
                  </div>
                  <div>
                    <dt>Price</dt>
                    <dd>{item.price || '-'}</dd>
                  </div>
                </dl>
              </div>
              <div className="survey-row-actions">
                <button className="secondary-action" type="button" onClick={() => toggleSurveyItem(item.id)}>
                  {selectedSurveyItemIds.includes(item.id) ? 'Selected' : 'Select'}
                </button>
                <button className="primary-action" type="button" onClick={() => openSurveyResponse(item)}>
                  Review Item
                </button>
              </div>
            </article>
          ))}
          {!surveyLoading && surveyItems.length === 0 && (
            <div className="empty-state">No survey items found.</div>
          )}
        </section>

        {isSurveyFormOpen && selectedSurveyItemIds.length > 0 && (
          <div className="modal-backdrop" role="presentation">
            <form className="modal-card survey-response-card" onSubmit={submitSurveyResponse}>
              <div>
                <p className="eyebrow">Survey Response</p>
                <h2>
                  {selectedSurveyItems.length === 1
                    ? selectedSurveyItems[0].item_name
                    : `${selectedSurveyItems.length} Selected Items`}
                </h2>
                <p className="subtitle">
                  {selectedSurveyItems.length === 1
                    ? `${selectedSurveyItems[0].brand}${
                        selectedSurveyItems[0].channel ? ` | ${selectedSurveyItems[0].channel}` : ''
                      }`
                    : 'These answers will be saved for every selected item.'}
                </p>
              </div>

              <label className="survey-question">
                Your email
                <input
                  required
                  type="email"
                  value={surveyForm.email}
                  onChange={(event) =>
                    setSurveyForm((current) => ({ ...current, email: event.target.value }))
                  }
                  placeholder="name@example.com"
                />
              </label>

              <fieldset className="survey-question">
                <legend>How effective is this item at attracting customer attention?</legend>
                {ratingOptions('attention_effectiveness', 'Scale: 1 (Not at all) - 5 (Extremely effective)')}
              </fieldset>

              <fieldset className="survey-question">
                <legend>Based on your experience, would you recommend rolling this item out widely?</legend>
                <div className="survey-radio-stack">
                  {(['Yes', 'No', 'Maybe'] as const).map((value) => (
                    <label key={value}>
                      <input
                        checked={surveyForm.recommend_rollout === value}
                        name="recommend_rollout"
                        type="radio"
                        value={value}
                        onChange={() => setSurveyForm((current) => ({ ...current, recommend_rollout: value }))}
                      />
                      {value}
                    </label>
                  ))}
                </div>
              </fieldset>

              <fieldset className="survey-question">
                <legend>Does this item drive noticeable engagement or interest at retail?</legend>
                {ratingOptions('retail_engagement', 'Scale: 1 (None) - 5 (High engagement)')}
              </fieldset>

              <fieldset className="survey-question">
                <legend>Does this item stand out compared to other displays or materials you see in-store in your market?</legend>
                <div className="survey-radio-stack">
                  {(['Yes', 'No', 'Neutral'] as const).map((value) => (
                    <label key={value}>
                      <input
                        checked={surveyForm.stands_out === value}
                        name="stands_out"
                        type="radio"
                        value={value}
                        onChange={() => setSurveyForm((current) => ({ ...current, stands_out: value }))}
                      />
                      {value}
                    </label>
                  ))}
                </div>
              </fieldset>

              <fieldset className="survey-question">
                <legend>Does the price seem reasonable?</legend>
                <div className="survey-radio-stack">
                  {(['Yes', 'No'] as const).map((value) => (
                    <label key={value}>
                      <input
                        checked={surveyForm.price_reasonable === value}
                        name="price_reasonable"
                        type="radio"
                        value={value}
                        onChange={() => setSurveyForm((current) => ({ ...current, price_reasonable: value }))}
                      />
                      {value}
                    </label>
                  ))}
                </div>
              </fieldset>

              <label className="survey-question">
                What are your overall thoughts / feedback about this item?
                <textarea
                  value={surveyForm.feedback}
                  onChange={(event) =>
                    setSurveyForm((current) => ({ ...current, feedback: event.target.value }))
                  }
                />
              </label>

              <div className="modal-actions">
                <button type="button" onClick={closeSurveyResponse}>
                  Cancel
                </button>
                <button className="primary-action" type="submit" disabled={isSubmittingSurvey}>
                  {isSubmittingSurvey ? 'Submitting...' : 'Submit'}
                </button>
              </div>
            </form>
          </div>
        )}
      </main>
    )
  }

  if (isPublicViewerPath) {
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
              Viewer Summary
            </button>
            <button
              className={isPublicKanbanPath ? 'active' : ''}
              type="button"
              onClick={() => goToPath('/viewer/kanban')}
            >
              Kanban Board
            </button>
            <button type="button" onClick={() => goToPath('/survey')}>
              Survey
            </button>
            <button type="button" onClick={() => goToPath('/')}>
              Sign In
            </button>
          </nav>
        </header>

        <section className="client-title-row viewer-title-row">
          <div className="client-title-copy">
            <div className="client-title-divider" />
            <div>
              <p className="eyebrow">Order Interest Viewer</p>
              <h1>{isPublicKanbanPath ? 'Viewer Kanban' : 'Viewer Dashboard'}</h1>
              <p className="subtitle">
                Ad Hoc and Buy period items summarized for public review.
              </p>
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
          <div className="viewer-toolbar-actions">
            <button
              className="secondary-action"
              type="button"
              onClick={() => setPublicQuery('')}
            >
              Reset Search
            </button>
            <span>{publicLoading ? 'Loading...' : `${publicItems.length.toLocaleString()} items`}</span>
          </div>
        </section>

        {publicError && <div className="error">{publicError}</div>}

        <section className="viewer-summary" aria-label="Viewer status summary">
          <article className="on-track">
            <span>On Track</span>
            <strong>{publicCounts['on-track']}</strong>
          </article>
          <article className="not-on-track">
            <span>Not On Track</span>
            <strong>{publicCounts['not-on-track']}</strong>
          </article>
        </section>

        {isPublicKanbanPath ? (
          <section className="kanban-board" aria-label="Viewer kanban board">
            {publicKanbanColumns.map(({ column, items }) => (
              <div key={column.id} className="kanban-column">
                <div className="kanban-column-header">
                  <h2>{column.title}</h2>
                  <span>{items.length}</span>
                </div>
                <div className="kanban-column-cards">
                  {items.map((item) => publicCard(item))}
                  {!publicLoading && items.length === 0 && (
                    <div className="empty-state">No items in this lane.</div>
                  )}
                </div>
              </div>
            ))}
          </section>
        ) : (
          <section className="viewer-sections" aria-label="Viewer item sections">
            {PUBLIC_SHEET_ORDER.map((sheet) => (
              <section key={sheet} className="viewer-section">
                <div className="viewer-section-header">
                  <div>
                    <p className="eyebrow">{publicSheetLabel(sheet)}</p>
                    <h2>
                      {sheet === 'ad-hoc'
                        ? 'Outside Buy Period Requests'
                        : 'Buy Period Requests'}
                    </h2>
                  </div>
                  <button type="button" onClick={() => goToPath('/viewer/kanban')}>
                    Open Kanban Board
                  </button>
                </div>
                <div className="viewer-card-grid">
                  {(publicItemsBySheet[sheet] ?? []).map((item) => publicCard(item))}
                  {!publicLoading && (publicItemsBySheet[sheet] ?? []).length === 0 && (
                    <div className="empty-state">No viewer items found for this section.</div>
                  )}
                </div>
              </section>
            ))}
          </section>
        )}
      </main>
    )
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
                Admin Settings
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
          <button type="button" onClick={() => goToPath('/viewer')}>
            Viewer
          </button>
          <button type="button" onClick={() => goToPath('/survey')}>
            Survey
          </button>
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
                ? 'Admin Settings'
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
          {kanbanMessage && <div className="success">{kanbanMessage}</div>}

          <div className="admin-tabs" role="tablist" aria-label="Admin settings sections">
            <button
              className={adminSettingsTab === 'users' ? 'active' : ''}
              type="button"
              onClick={() => setAdminSettingsTab('users')}
            >
              Manage Users
            </button>
            <button
              className={adminSettingsTab === 'statuses' ? 'active' : ''}
              type="button"
              onClick={() => setAdminSettingsTab('statuses')}
            >
              Manage Statuses
            </button>
            <button
              className={adminSettingsTab === 'kanban' ? 'active' : ''}
              type="button"
              onClick={() => setAdminSettingsTab('kanban')}
            >
              Kanban Settings
            </button>
          </div>

          {adminSettingsTab === 'users' && (
            <>
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
            </>
          )}

          {adminSettingsTab === 'statuses' && (
          <section className="admin-create-card status-settings-card">
            <form onSubmit={createSheetStatus}>
              <h2>Manage Statuses</h2>
              <label>
                Sheet
                <select
                  value={newSheetStatus.sheet}
                  onChange={(event) =>
                    setNewSheetStatus((current) => ({
                      ...current,
                      sheet: event.target.value as StatusTargetSheet,
                    }))
                  }
                >
                  <option value="all">All Sheets</option>
                  {SHEET_ORDER.map((sheetKey) => (
                    <option key={sheetKey} value={sheetKey}>
                      {sheets?.[sheetKey]?.label ?? sheetKey}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                New Status
                <input
                  required
                  value={newSheetStatus.status}
                  onChange={(event) =>
                    setNewSheetStatus((current) => ({ ...current, status: event.target.value }))
                  }
                  placeholder="New status label"
                />
              </label>
              <button className="primary-action" type="submit">
                Add Status
              </button>
            </form>

            <div className="status-settings-list">
              <div className="table-tools">
                <span>
                  {statusesLoading ? 'Loading statuses...' : `${sheetStatuses.length} tracker statuses`}
                </span>
                <button type="button" onClick={loadSheetStatuses}>
                  Refresh
                </button>
              </div>
              {SHEET_ORDER.map((sheetKey) => {
                const rows = sheetStatuses.filter((statusRow) => statusRow.sheet === sheetKey)
                return (
                  <div key={sheetKey} className="status-group">
                    <h3>{sheets?.[sheetKey]?.label ?? sheetKey}</h3>
                    <div className="selected-status-chips removable-status-chips">
                      {rows.map((statusRow) => (
                        <span key={statusRow.id}>
                          {statusRow.status}
                          <button type="button" onClick={() => deleteSheetStatus(statusRow)}>
                            Remove
                          </button>
                        </span>
                      ))}
                      {!rows.length && <em>No statuses yet.</em>}
                    </div>
                  </div>
                )
              })}
            </div>
          </section>
          )}

          {adminSettingsTab === 'users' && (
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
          )}

          {adminSettingsTab === 'kanban' && (
        <section className="kanban-settings-page">
          <form className="admin-create-card kanban-create-card" onSubmit={createKanbanColumn}>
            <h2>Create Kanban Column</h2>
            <label>
              Header
              <input
                required
                value={newKanbanColumn.title}
                onChange={(event) =>
                  setNewKanbanColumn((current) => ({ ...current, title: event.target.value }))
                }
                placeholder="Needs Details"
              />
            </label>
            <label className="wide-field">
              Statuses
              {renderStatusMultiSelect(newKanbanColumn.statuses, (nextStatuses) =>
                setNewKanbanColumn((current) => ({ ...current, statuses: nextStatuses })),
              )}
            </label>
            <label>
              Order
              <input
                type="number"
                value={newKanbanColumn.sort_order}
                onChange={(event) =>
                  setNewKanbanColumn((current) => ({
                    ...current,
                    sort_order: Number(event.target.value),
                  }))
                }
              />
            </label>
            <label className="checkbox-label">
              <input
                checked={newKanbanColumn.is_visible}
                type="checkbox"
                onChange={(event) =>
                  setNewKanbanColumn((current) => ({ ...current, is_visible: event.target.checked }))
                }
              />
              Visible
            </label>
            <button className="primary-action" type="submit">
              Create Column
            </button>
          </form>

          <section className="table-card kanban-settings-card">
            <div className="table-tools">
              <span>
                {kanbanColumnsLoading
                  ? 'Loading kanban columns...'
                  : `${kanbanColumns.length} kanban columns`}
              </span>
              <button type="button" onClick={() => loadKanbanColumns(true)}>
                Refresh
              </button>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Header</th>
                    <th>Statuses</th>
                    <th>Order</th>
                    <th>Visible</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {kanbanColumns.map((column) => (
                    <tr key={column.id}>
                      <td>
                        <input
                          value={column.title}
                          onBlur={(event) => updateKanbanColumn(column.id, { title: event.target.value })}
                          onChange={(event) =>
                            setKanbanColumns((current) =>
                              current.map((row) =>
                                row.id === column.id ? { ...row, title: event.target.value } : row,
                              ),
                            )
                          }
                        />
                      </td>
                      <td>
                        {renderStatusMultiSelect(column.statuses, (nextStatuses) =>
                          updateKanbanColumn(column.id, { statuses: nextStatuses }),
                        )}
                      </td>
                      <td>
                        <input
                          type="number"
                          value={column.sort_order}
                          onBlur={(event) =>
                            updateKanbanColumn(column.id, { sort_order: Number(event.target.value) })
                          }
                          onChange={(event) =>
                            setKanbanColumns((current) =>
                              current.map((row) =>
                                row.id === column.id
                                  ? { ...row, sort_order: Number(event.target.value) }
                                  : row,
                              ),
                            )
                          }
                        />
                      </td>
                      <td className="actions">
                        <input
                          checked={column.is_visible}
                          type="checkbox"
                          onChange={(event) =>
                            updateKanbanColumn(column.id, { is_visible: event.target.checked })
                          }
                        />
                      </td>
                      <td className="actions">
                        <button type="button" onClick={() => deleteKanbanColumn(column.id)}>
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!kanbanColumnsLoading && kanbanColumns.length === 0 && (
                    <tr>
                      <td colSpan={5} className="empty-state">
                        No kanban columns yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </section>
          )}
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
            <button className="secondary-action" type="button" onClick={() => goToPath('/viewer/kanban')}>
              Viewer Kanban
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
                          {isImageReference(addForm.visual_reference) && (
                            <a
                              className="visual-reference-preview"
                              href={addForm.visual_reference}
                              target="_blank"
                              rel="noreferrer"
                            >
                              <img src={addForm.visual_reference} alt="Visual reference preview" />
                            </a>
                          )}
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
                              {isImageReference(value) && (
                                <a
                                  className="visual-reference-preview"
                                  href={value}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  <img src={value} alt="Visual reference preview" />
                                </a>
                              )}
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
