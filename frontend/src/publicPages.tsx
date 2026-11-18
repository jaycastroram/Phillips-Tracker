import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import type {
  KanbanColumn,
  SurveyFilterField,
  SurveyInterest,
  SurveyItem,
  SurveyAnswerValue,
  SurveyQuestion,
  SurveyResponseDraft,
  TrackerItem,
} from './types'
import { DEFAULT_SURVEY_QUESTIONS } from './constants'
import {
  getViewerStatus,
  isImageReference,
  publicSheetLabel,
  viewerStatusLabel,
} from './utils'

const BUY_BOOK_HEADER_LOGO = '/SmartBuy Analytics -Buy Book Logo (1).png'
const PRE_BUY_SURVEY_HEADER_LOGO = '/SmartBuy Analytics - Pre Buy Survey Logo.png'
const STATUS_TRACKER_HEADER_LOGO = '/SmartBuy Analytics - Status Tracker Logo.png'
const MRL_LOGO_URL =
  'https://4119972-sb3.app.netsuite.com/core/media/media.nl?id=572741&c=4119972_SB3&h=OS3XIzw1DV8O7jsRJka2EAdoeYoit8KRvaVkv-a-x39sSZ3v'

type PublicKanbanColumn = {
  column: KanbanColumn
  items: TrackerItem[]
}

type PublicBrandProps = {
  companyName?: string
  companyLogoUrl?: string
  companyBannerUrl?: string
}

type BuyBookPageProps = PublicBrandProps & {
  surveyItems: SurveyItem[]
  surveyFilterFields: SurveyFilterField[]
  surveyLoading: boolean
  surveyError: string
  surveyQuery: string
  selectedSurveyItemIds: number[]
  setSurveyQuery: Dispatch<SetStateAction<string>>
  toggleSurveyItem: (itemId: number) => void
  printSurveyBuyBook: () => void
}

type SurveyPageProps = PublicBrandProps & {
  surveyItems: SurveyItem[]
  surveyFilterFields: SurveyFilterField[]
  surveyQuestions: SurveyQuestion[]
  surveyLoading: boolean
  surveyError: string
  surveyMessage: string
  surveyQuery: string
  selectedSurveyItemIds: number[]
  isSubmittingSurvey: boolean
  surveyEmail: string
  setSurveyEmail: (email: string) => void
  setSurveyQuery: Dispatch<SetStateAction<string>>
  surveyOpen?: boolean
  surveyClosedMessage?: string
  toggleSurveyItem: (itemId: number) => void
  submitSurveyResponse: () => void
  bulkUpdateSurveyInterest: (interest: Exclude<SurveyInterest, ''>) => void
  getSurveyResponse: (itemId: number) => SurveyResponseDraft
  updateSurveyResponse: (itemId: number, updates: Partial<SurveyResponseDraft>) => void
}

const INTEREST_TYPES: SurveyQuestion['type'][] = ['yes_no', 'single_choice', 'dropdown']

function findInterestQuestion(questions: SurveyQuestion[]) {
  return (
    questions.find((question) => question.id === 'interest' && INTEREST_TYPES.includes(question.type)) ??
    questions.find((question) => INTEREST_TYPES.includes(question.type)) ??
    null
  )
}

function SurveyQuestionFields({
  itemId,
  questions,
  draft,
  onAnswer,
}: {
  itemId: number
  questions: SurveyQuestion[]
  draft: SurveyResponseDraft
  onAnswer: (questionId: string, value: SurveyAnswerValue) => void
}) {
  return (
    <div className="survey-dynamic-questions">
      {questions.map((question) => {
        const value = draft.answers[question.id]
        if (question.type === 'text') {
          return (
            <label key={question.id} className="survey-feedback-field">
              <span>
                {question.label}
                {question.required ? ' *' : ''}
              </span>
              <textarea
                value={typeof value === 'string' || typeof value === 'number' ? String(value) : ''}
                onChange={(event) => onAnswer(question.id, event.target.value)}
                placeholder={question.label}
              />
            </label>
          )
        }

        if (question.type === 'scale') {
          return (
            <fieldset key={question.id}>
              <legend>
                {question.label}
                {question.required ? ' *' : ''}
              </legend>
              <div className="survey-scale-options">
                {[1, 2, 3, 4, 5].map((score) => (
                  <label key={score}>
                    <input
                      checked={Number(value) === score}
                      type="radio"
                      name={`item-${itemId}-${question.id}-scale`}
                      onChange={() => onAnswer(question.id, score)}
                    />
                    {score}
                  </label>
                ))}
              </div>
            </fieldset>
          )
        }

        const options =
          question.options.length > 0
            ? question.options
            : question.type === 'yes_no'
              ? ['Yes', 'No']
              : []

        if (question.type === 'dropdown') {
          return (
            <label key={question.id} className="survey-dropdown-field">
              <span>
                {question.label}
                {question.required ? ' *' : ''}
              </span>
              <select
                value={typeof value === 'string' ? value : ''}
                onChange={(event) => onAnswer(question.id, event.target.value)}
              >
                <option value="">Select an answer…</option>
                {options.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
          )
        }

        if (question.type === 'checkbox') {
          const selected = Array.isArray(value) ? value : value ? [String(value)] : []
          return (
            <fieldset key={question.id}>
              <legend>
                {question.label}
                {question.required ? ' *' : ''}
              </legend>
              {options.map((option) => (
                <label key={option}>
                  <input
                    checked={selected.includes(option)}
                    type="checkbox"
                    value={option}
                    onChange={() =>
                      onAnswer(
                        question.id,
                        selected.includes(option)
                          ? selected.filter((picked) => picked !== option)
                          : [...selected, option],
                      )
                    }
                  />
                  {option}
                </label>
              ))}
            </fieldset>
          )
        }

        return (
          <fieldset key={question.id}>
            <legend>
              {question.label}
              {question.required ? ' *' : ''}
            </legend>
            {options.map((option) => (
              <label key={option}>
                <input
                  checked={String(value ?? '') === option}
                  name={`item-${itemId}-${question.id}`}
                  type="radio"
                  value={option}
                  onChange={() => onAnswer(question.id, option)}
                />
                {option}
              </label>
            ))}
          </fieldset>
        )
      })}
    </div>
  )
}

type ViewerPageProps = PublicBrandProps & {
  isPublicKanbanPath: boolean
  gridPath: string
  kanbanPath: string
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

function ImageLightbox({
  imageUrl,
  title,
  onClose,
}: {
  imageUrl: string
  title: string
  onClose: () => void
}) {
  return (
    <div className="image-lightbox-backdrop" role="presentation" onClick={onClose}>
      <div className="image-lightbox-card" role="dialog" aria-modal="true" aria-label={title}>
        <button className="image-lightbox-close" type="button" onClick={onClose}>
          Close
        </button>
        <img src={imageUrl} alt={title} onClick={(event) => event.stopPropagation()} />
      </div>
    </div>
  )
}

function PublicCard({
  item,
  onImageClick,
}: {
  item: TrackerItem
  onImageClick: (imageUrl: string, title: string) => void
}) {
  return (
    <article key={item.id} className={`viewer-card ${getViewerStatus(item)}`}>
      <div className="viewer-card-topline">
        <span>{publicSheetLabel(item.sheet)}</span>
        <strong>{viewerStatusLabel(getViewerStatus(item))}</strong>
      </div>
      {isImageReference(item.visual_reference) && (
        <button
          className="viewer-card-image image-preview-trigger"
          type="button"
          onClick={() => onImageClick(item.visual_reference, item.item_name || 'Visual reference')}
        >
          <img src={item.visual_reference} alt="" />
        </button>
      )}
      <h3>{item.item_name || 'Untitled Item'}</h3>
      <p className="viewer-card-meta">
        {[item.brand, item.program_name, item.extra?.product_type].filter(Boolean).join(' | ') ||
          'No brand/program listed'}
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

function PublicBrandHeader({
  src,
  alt,
  isCompanyBrand = false,
}: {
  src: string
  alt: string
  isCompanyBrand?: boolean
}) {
  return (
    <header className={`public-brand-art${isCompanyBrand ? ' is-company-brand' : ''}`}>
      <img src={src} alt={alt} />
    </header>
  )
}

function resolvePublicBrandHeader(
  companyBannerUrl: string | undefined,
  companyLogoUrl: string | undefined,
  companyName: string | undefined,
  fallbackSrc: string,
  fallbackAlt: string,
) {
  const banner = companyBannerUrl?.trim() || ''
  const logo = companyLogoUrl?.trim() || ''
  if (banner) {
    return { src: banner, alt: companyName || fallbackAlt, isCompanyBrand: true }
  }
  if (logo) {
    return { src: logo, alt: companyName || fallbackAlt, isCompanyBrand: true }
  }
  return { src: fallbackSrc, alt: fallbackAlt, isCompanyBrand: false }
}

function PublicFooter() {
  return (
    <footer className="public-page-footer" aria-label="Partner logos">
      <img src={MRL_LOGO_URL} alt="MRL" />
    </footer>
  )
}

function getSurveyProductType(item: SurveyItem) {
  if (item.product_type) return item.product_type

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

function useSurveyFilters(items: SurveyItem[], enabledFields: SurveyFilterField[]) {
  const [brandFilter, setBrandFilter] = useState('')
  const [channelFilter, setChannelFilter] = useState('')
  const [productTypeFilter, setProductTypeFilter] = useState('')
  const [keyProgramFilter, setKeyProgramFilter] = useState('')

  const brandOptions = useMemo(() => uniqueOptions(items.map((item) => item.brand)), [items])
  const channelOptions = useMemo(() => uniqueOptions(items.map((item) => item.channel)), [items])
  const productTypeOptions = useMemo(
    () => uniqueOptions(items.map((item) => getSurveyProductType(item))),
    [items],
  )
  const keyProgramOptions = useMemo(() => uniqueOptions(items.map((item) => item.key_program)), [items])
  const filteredItems = useMemo(() => {
    return items.filter((item) => {
      if (enabledFields.includes('brand') && brandFilter && item.brand !== brandFilter) return false
      if (enabledFields.includes('channel') && channelFilter && item.channel !== channelFilter) return false
      if (
        enabledFields.includes('product_type') &&
        productTypeFilter &&
        getSurveyProductType(item) !== productTypeFilter
      ) {
        return false
      }
      if (enabledFields.includes('key_program') && keyProgramFilter && item.key_program !== keyProgramFilter) {
        return false
      }
      return true
    })
  }, [brandFilter, channelFilter, enabledFields, items, keyProgramFilter, productTypeFilter])

  return {
    brandFilter,
    brandOptions,
    channelFilter,
    channelOptions,
    enabledFields,
    filteredItems,
    keyProgramFilter,
    keyProgramOptions,
    productTypeFilter,
    productTypeOptions,
    resetFilters: () => {
      setBrandFilter('')
      setChannelFilter('')
      setProductTypeFilter('')
      setKeyProgramFilter('')
    },
    setBrandFilter,
    setChannelFilter,
    setKeyProgramFilter,
    setProductTypeFilter,
  }
}

function groupSurveyPages(items: SurveyItem[]) {
  const pages: SurveyItem[][] = []
  let current: SurveyItem[] = []
  items.forEach((item, index) => {
    if (item.page_break_before && index > 0 && current.length > 0) {
      pages.push(current)
      current = []
    }
    current.push(item)
  })
  if (current.length > 0 || pages.length === 0) pages.push(current)
  return pages
}

function SurveyFilterControls({
  brandFilter,
  brandOptions,
  channelFilter,
  channelOptions,
  enabledFields,
  keyProgramFilter,
  keyProgramOptions,
  productTypeFilter,
  productTypeOptions,
  setBrandFilter,
  setChannelFilter,
  setKeyProgramFilter,
  setProductTypeFilter,
}: ReturnType<typeof useSurveyFilters>) {
  return (
    <div className="survey-filter-grid">
      {enabledFields.includes('brand') && (
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
      )}
      {enabledFields.includes('channel') && (
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
      )}
      {enabledFields.includes('product_type') && (
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
      )}
      {enabledFields.includes('key_program') && (
        <label>
          Key Program
          <select value={keyProgramFilter} onChange={(event) => setKeyProgramFilter(event.target.value)}>
            <option value="">All key programs</option>
            {keyProgramOptions.map((keyProgram) => (
              <option key={keyProgram} value={keyProgram}>
                {keyProgram}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  )
}

export function BuyBookPage({
  companyName,
  companyLogoUrl,
  companyBannerUrl,
  surveyItems,
  surveyFilterFields,
  surveyLoading,
  surveyError,
  surveyQuery,
  selectedSurveyItemIds,
  setSurveyQuery,
  toggleSurveyItem,
  printSurveyBuyBook,
}: BuyBookPageProps) {
  const surveyFilters = useSurveyFilters(surveyItems, surveyFilterFields)
  const [previewImage, setPreviewImage] = useState<{ url: string; title: string } | null>(null)
  const [currentPage, setCurrentPage] = useState(0)
  const pages = useMemo(() => groupSurveyPages(surveyFilters.filteredItems), [surveyFilters.filteredItems])
  const safePage = Math.min(currentPage, Math.max(pages.length - 1, 0))
  const brandHeader = resolvePublicBrandHeader(
    companyBannerUrl,
    companyLogoUrl,
    companyName,
    BUY_BOOK_HEADER_LOGO,
    'SmartBuy Buy Book',
  )
  useEffect(() => {
    setCurrentPage(0)
  }, [surveyFilters.filteredItems])
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
      <PublicBrandHeader {...brandHeader} />

      <section className="viewer-toolbar buy-book-toolbar">
        <div className="buy-book-filter-group">
          <label>
            Search
            <input
              value={surveyQuery}
              onChange={(event) => setSurveyQuery(event.target.value)}
              placeholder="Item, brand, channel, description..."
            />
          </label>
          <SurveyFilterControls {...surveyFilters} />
        </div>
        <div className="viewer-toolbar-actions buy-book-toolbar-actions">
          <button
            className="secondary-action"
            type="button"
            onClick={() => {
              setSurveyQuery('')
              surveyFilters.resetFilters()
            }}
          >
            Reset Search
          </button>
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

      <div className="survey-pager">
        <button
          className="secondary-action"
          type="button"
          disabled={safePage === 0}
          onClick={() => setCurrentPage(Math.max(safePage - 1, 0))}
        >
          Back
        </button>
        <span>
          Page {pages.length === 0 ? 0 : safePage + 1} of {pages.length}
        </span>
        <button
          className="secondary-action"
          type="button"
          disabled={safePage >= pages.length - 1}
          onClick={() => setCurrentPage(Math.min(safePage + 1, pages.length - 1))}
        >
          Next
        </button>
      </div>

      {surveyError && <div className="error">{surveyError}</div>}

      <section className="survey-grid" aria-label="Buy book items">
        {surveyFilters.filteredItems.map((item, index) => {
          const pageIndex = pages.findIndex((page) => page.some((pageItem) => pageItem.id === item.id))
          const previous = index > 0 ? surveyFilters.filteredItems[index - 1] : null
          // The backend derives both the headings and the breaks from the layer
          // stack; older payloads without them fall back to the single group.
          const headers =
            item.group_headers && item.group_headers.length > 0
              ? item.group_headers
              : item.group_label && (!previous || previous.group_label !== item.group_label || item.page_break_before)
                ? [{ field: item.group_field ?? 'brand', label: item.group_label }]
                : []
          const showGroupHeader = headers.length > 0
          const isOnPage = pageIndex === safePage
          return (
            <div key={item.id} className={`buy-book-item-block${isOnPage ? '' : ' survey-page-hidden'}`}>
              {showGroupHeader && (
                <header className={`buy-book-group-header${item.page_break_before && index > 0 ? ' survey-page-break' : ''}`}>
                  {headers.map((header, depth) => {
                    const facetLabel =
                      header.field === 'key_program'
                        ? 'Program'
                        : header.field === 'product_type'
                          ? 'Type'
                          : header.field === 'channel'
                            ? 'Channel'
                            : 'Brand'
                    const level = header.level ?? depth
                    return level === 0 ? (
                      <div key={`${header.field}-${level}`} className="buy-book-group-level buy-book-group-level--0">
                        <p className="eyebrow">{facetLabel}</p>
                        <h2>{header.label}</h2>
                      </div>
                    ) : (
                      <p key={`${header.field}-${level}`} className="buy-book-group-sub">
                        <span>{facetLabel}</span>
                        {header.label}
                      </p>
                    )
                  })}
                </header>
              )}
              <article
                className={`survey-card buy-book-card${!showGroupHeader && item.page_break_before && index > 0 ? ' survey-page-break' : ''}`}
              >
                <label className="survey-select">
                  <input
                    checked={selectedSurveyItemIds.includes(item.id)}
                    type="checkbox"
                    onChange={() => toggleSurveyItem(item.id)}
                  />
                  <span>Select</span>
                </label>
                <button
                  className="survey-image image-preview-trigger"
                  type="button"
                  onClick={() => {
                    if (isImageReference(item.image_url)) setPreviewImage({ url: item.image_url, title: item.item_name })
                  }}
                >
                  {isImageReference(item.image_url) ? (
                    <img src={item.image_url} alt="" />
                  ) : (
                    <span>{item.brand.slice(0, 2).toUpperCase() || 'SB'}</span>
                  )}
                </button>
                <div className="survey-card-copy">
                  <span className="survey-column-heading">Item Name</span>
                  <h2>{item.item_name}</h2>
                  <p>{item.item_description || 'No description provided yet.'}</p>
                </div>
              </article>
            </div>
          )
        })}
        {!surveyLoading && surveyFilters.filteredItems.length === 0 && (
          <div className="empty-state">No buy book items found.</div>
        )}
      </section>
      <PublicFooter />
      {previewImage && (
        <ImageLightbox
          imageUrl={previewImage.url}
          title={previewImage.title}
          onClose={() => setPreviewImage(null)}
        />
      )}
    </main>
  )
}

export function SurveyPage({
  companyName,
  companyLogoUrl,
  companyBannerUrl,
  surveyItems,
  surveyFilterFields,
  surveyQuestions,
  surveyLoading,
  surveyError,
  surveyMessage,
  surveyQuery,
  selectedSurveyItemIds,
  isSubmittingSurvey,
  surveyEmail,
  setSurveyEmail,
  setSurveyQuery,
  surveyOpen = true,
  surveyClosedMessage = 'No survey is open right now.',
  toggleSurveyItem,
  submitSurveyResponse,
  bulkUpdateSurveyInterest,
  getSurveyResponse,
  updateSurveyResponse,
}: SurveyPageProps) {
  const surveyFilters = useSurveyFilters(surveyItems, surveyFilterFields)
  const questions = surveyQuestions.length > 0 ? surveyQuestions : DEFAULT_SURVEY_QUESTIONS
  const interestQuestion = findInterestQuestion(questions)
  const [previewImage, setPreviewImage] = useState<{ url: string; title: string } | null>(null)
  const [currentPage, setCurrentPage] = useState(0)
  const pages = useMemo(() => groupSurveyPages(surveyFilters.filteredItems), [surveyFilters.filteredItems])
  const safePage = Math.min(currentPage, Math.max(pages.length - 1, 0))
  const brandHeader = resolvePublicBrandHeader(
    companyBannerUrl,
    companyLogoUrl,
    companyName,
    PRE_BUY_SURVEY_HEADER_LOGO,
    'SmartBuy Pre Buy Survey',
  )
  useEffect(() => {
    setCurrentPage(0)
  }, [surveyFilters.filteredItems])
  const allVisibleSelected =
    surveyFilters.filteredItems.length > 0 &&
    surveyFilters.filteredItems.every((item) => selectedSurveyItemIds.includes(item.id))

  function toggleVisibleSurveyItems() {
    surveyFilters.filteredItems.forEach((item) => {
      const isSelected = selectedSurveyItemIds.includes(item.id)
      if (allVisibleSelected ? isSelected : !isSelected) toggleSurveyItem(item.id)
    })
  }

  function answerQuestion(itemId: number, questionId: string, value: SurveyAnswerValue) {
    if (!selectedSurveyItemIds.includes(itemId)) toggleSurveyItem(itemId)
    const draft = getSurveyResponse(itemId)
    const nextAnswers = { ...draft.answers, [questionId]: value }
    const updates: Partial<SurveyResponseDraft> = { answers: nextAnswers }
    if (interestQuestion && questionId === interestQuestion.id) {
      const yes = interestQuestion.options[0] || 'Yes'
      const no = interestQuestion.options[1] || 'No'
      updates.interest =
        value === yes ? 'Interested' : value === no ? 'Not Interested' : ''
    }
    const textQuestion = questions.find((question) => question.type === 'text' && question.id === questionId)
    if (textQuestion) {
      updates.notes = String(value ?? '')
    }
    updateSurveyResponse(itemId, updates)
  }

  if (!surveyLoading && !surveyOpen) {
    return (
      <main className="app-shell viewer-shell survey-shell">
        <PublicBrandHeader {...brandHeader} />
        <div className="empty-state survey-closed-state">{surveyClosedMessage}</div>
        <PublicFooter />
      </main>
    )
  }

  return (
    <main className="app-shell viewer-shell survey-shell">
      <PublicBrandHeader {...brandHeader} />

      <section className="survey-toolbar">
        {/* Row 1: Search + Filters */}
        <div className="survey-toolbar-filters">
          <label>
            Search
            <input
              value={surveyQuery}
              onChange={(event) => setSurveyQuery(event.target.value)}
              placeholder="Item, brand, channel, description..."
            />
          </label>
          <SurveyFilterControls {...surveyFilters} />
          <button
            className="secondary-action"
            type="button"
            onClick={() => {
              setSurveyQuery('')
              surveyFilters.resetFilters()
            }}
          >
            Reset
          </button>
          <span className="survey-item-count">
            {surveyLoading ? 'Loading...' : `${surveyFilters.filteredItems.length.toLocaleString()} items`}
          </span>
        </div>

        {/* Row 2: Selection actions + Email + Submit */}
        <div className="survey-toolbar-actions">
          <button
            className="secondary-action"
            type="button"
            onClick={toggleVisibleSurveyItems}
            disabled={surveyFilters.filteredItems.length === 0}
          >
            {allVisibleSelected ? 'Clear Selection' : 'Select All'}
          </button>
          {interestQuestion && (
            <>
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
            </>
          )}
          <div className="survey-toolbar-spacer" />
          <label className="survey-email-field">
            <span>Your Email <span className="survey-email-required" aria-label="required">*</span></span>
            <input
              autoComplete="email"
              inputMode="email"
              placeholder="you@example.com"
              required
              type="email"
              value={surveyEmail}
              onChange={(event) => setSurveyEmail(event.target.value)}
            />
          </label>
          <button className="secondary-action" type="button" onClick={() => window.print()}>
            Print Survey
          </button>
          <button
            className="primary-action"
            type="button"
            onClick={submitSurveyResponse}
            disabled={selectedSurveyItemIds.length === 0 || isSubmittingSurvey}
          >
            {isSubmittingSurvey ? 'Submitting...' : `Submit Selected (${selectedSurveyItemIds.length})`}
          </button>
        </div>
      </section>

      <div className="survey-pager">
        <button
          className="secondary-action"
          type="button"
          disabled={safePage === 0}
          onClick={() => setCurrentPage(Math.max(safePage - 1, 0))}
        >
          Back
        </button>
        <span>
          Page {pages.length === 0 ? 0 : safePage + 1} of {pages.length}
        </span>
        <button
          className="secondary-action"
          type="button"
          disabled={safePage >= pages.length - 1}
          onClick={() => setCurrentPage(Math.min(safePage + 1, pages.length - 1))}
        >
          Next
        </button>
      </div>

      {surveyError && <div className="error">{surveyError}</div>}
      {surveyMessage && <div className="success">{surveyMessage}</div>}

      <section className="survey-grid" aria-label="Survey items">
        {surveyFilters.filteredItems.map((item, index) => {
          const pageIndex = pages.findIndex((page) => page.some((pageItem) => pageItem.id === item.id))
          return (
          <article
            key={item.id}
            className={`survey-card${item.page_break_before && index > 0 ? ' survey-page-break' : ''}${
              pageIndex === safePage ? '' : ' survey-page-hidden'
            }`}
            style={{ '--response-width': `${item.response_width || 36}%` } as React.CSSProperties}
          >
            <label className="survey-select">
              <input
                checked={selectedSurveyItemIds.includes(item.id)}
                type="checkbox"
                onChange={() => toggleSurveyItem(item.id)}
              />
              <span>Select</span>
            </label>
            <button
              className="survey-image image-preview-trigger"
              type="button"
              onClick={() => {
                if (isImageReference(item.image_url)) setPreviewImage({ url: item.image_url, title: item.item_name })
              }}
            >
              {isImageReference(item.image_url) ? (
                <img src={item.image_url} alt="" />
              ) : (
                <span>{item.brand.slice(0, 2).toUpperCase() || 'SB'}</span>
              )}
            </button>
            <div className="survey-card-copy">
              <h2>{item.item_name}</h2>
              <p>{item.item_description || 'No description provided yet.'}</p>
            </div>
            <div className="survey-inline-response">
              <h3>Response</h3>
              <SurveyQuestionFields
                itemId={item.id}
                questions={questions}
                draft={getSurveyResponse(item.id)}
                onAnswer={(questionId, value) => answerQuestion(item.id, questionId, value)}
              />
            </div>
          </article>
          )
        })}
        {!surveyLoading && surveyFilters.filteredItems.length === 0 && (
          <div className="empty-state">No survey items found.</div>
        )}
      </section>
      <PublicFooter />
      {previewImage && (
        <ImageLightbox
          imageUrl={previewImage.url}
          title={previewImage.title}
          onClose={() => setPreviewImage(null)}
        />
      )}
    </main>
  )
}

export function ViewerPage({
  companyName,
  companyLogoUrl,
  companyBannerUrl,
  isPublicKanbanPath,
  gridPath,
  kanbanPath,
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
  const [previewImage, setPreviewImage] = useState<{ url: string; title: string } | null>(null)
  const brandHeader = resolvePublicBrandHeader(
    companyBannerUrl,
    companyLogoUrl,
    companyName,
    STATUS_TRACKER_HEADER_LOGO,
    'SmartBuy Status Tracker',
  )

  return (
    <main className="app-shell viewer-shell public-viewer-shell">
      <PublicBrandHeader {...brandHeader} />

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
          <div className="viewer-view-toggle" aria-label="Viewer display mode">
            <button
              className={!isPublicKanbanPath ? 'active' : ''}
              type="button"
              onClick={() => goToPath(gridPath)}
            >
              Grid
            </button>
            <button
              className={isPublicKanbanPath ? 'active' : ''}
              type="button"
              onClick={() => goToPath(kanbanPath)}
            >
              Kanban
            </button>
          </div>
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
                  <PublicCard
                    key={item.id}
                    item={item}
                    onImageClick={(url, title) => setPreviewImage({ url, title })}
                  />
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
                  <th>Product Type</th>
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
                        <button
                          className="viewer-grid-image-trigger image-preview-trigger"
                          type="button"
                          onClick={() =>
                            setPreviewImage({
                              url: item.visual_reference,
                              title: item.item_name || 'Visual reference',
                            })
                          }
                        >
                          <img className="viewer-grid-image" src={item.visual_reference} alt="" />
                        </button>
                      ) : (
                        item.visual_reference || '-'
                      )}
                    </td>
                    <td>{item.brand || '-'}</td>
                    <td>{item.program_name || '-'}</td>
                    <td>{item.extra?.product_type || '-'}</td>
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
      <PublicFooter />
      {previewImage && (
        <ImageLightbox
          imageUrl={previewImage.url}
          title={previewImage.title}
          onClose={() => setPreviewImage(null)}
        />
      )}
    </main>
  )
}
