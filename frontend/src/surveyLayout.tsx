import { useEffect, useState, type ReactNode } from 'react'
import { SURVEY_FILTER_FIELDS } from './constants'
import type { SurveyFilterField, SurveyItem } from './types'
import { isImageReference } from './utils'

type SurveyLayoutEditorProps = {
  items: SurveyItem[]
  filterFields: SurveyFilterField[]
  loading: boolean
  saving: boolean
  message: string
  title?: string
  description?: string
  showSurvey?: boolean
  showBuyBook?: boolean
  showResponseWidth?: boolean
  showImageControls?: boolean
  filterLegend?: string
  filterExtra?: ReactNode
  includeLabel?: string
  saveLabel?: string
  disabled?: boolean
  onSave: (items: SurveyItem[], filterFields: SurveyFilterField[]) => void
}

type ImageDisplaySize = 'small' | 'medium' | 'large' | 'full'
type ImageFit = 'contain' | 'cover'

type SurveyItemExtended = SurveyItem & {
  image_display_size?: ImageDisplaySize
  image_fit?: ImageFit
}

const IMAGE_SIZE_OPTIONS: { value: ImageDisplaySize; px: number; label: string }[] = [
  { value: 'small', px: 80, label: 'S' },
  { value: 'medium', px: 120, label: 'M' },
  { value: 'large', px: 200, label: 'L' },
  { value: 'full', px: 260, label: 'XL' },
]

function sizeIndex(value: ImageDisplaySize | undefined) {
  const index = IMAGE_SIZE_OPTIONS.findIndex((option) => option.value === value)
  return index >= 0 ? index : 1
}

function sizePx(value: ImageDisplaySize | undefined) {
  return IMAGE_SIZE_OPTIONS[sizeIndex(value)].px
}

export function SurveyLayoutEditor({
  items,
  filterFields,
  loading,
  saving,
  message,
  title = 'Survey Layout',
  description = 'Drag items into survey order. Page breaks split the survey on screen and when printing.',
  showSurvey = true,
  showBuyBook = true,
  showResponseWidth = true,
  showImageControls,
  filterLegend = 'Survey filters',
  filterExtra,
  includeLabel = 'Buy Book',
  saveLabel = 'Save Layout',
  disabled = false,
  onSave,
}: SurveyLayoutEditorProps) {
  const [draftItems, setDraftItems] = useState<SurveyItemExtended[]>(items as SurveyItemExtended[])
  const [draftFilters, setDraftFilters] = useState(filterFields)
  const [draggingId, setDraggingId] = useState<number | null>(null)
  const imageControlsEnabled = showImageControls ?? showBuyBook

  useEffect(() => {
    setDraftItems(items as SurveyItemExtended[])
    setDraftFilters(filterFields)
  }, [filterFields, items])

  function moveItem(sourceId: number, targetId: number) {
    if (sourceId === targetId) return
    setDraftItems((current) => {
      const fromIndex = current.findIndex((item) => item.id === sourceId)
      const toIndex = current.findIndex((item) => item.id === targetId)
      if (fromIndex < 0 || toIndex < 0) return current
      const next = [...current]
      const [moved] = next.splice(fromIndex, 1)
      next.splice(toIndex, 0, moved)
      return next.map((item, index) => ({
        ...item,
        survey_sort_order: index + 1,
        page_break_before: index === 0 ? false : item.page_break_before,
      }))
    })
  }

  function updateItem(itemId: number, updates: Partial<SurveyItemExtended>) {
    setDraftItems((current) =>
      current.map((item) => (item.id === itemId ? { ...item, ...updates } : item)),
    )
  }

  function toggleFilter(field: SurveyFilterField) {
    setDraftFilters((current) =>
      current.includes(field) ? current.filter((item) => item !== field) : [...current, field],
    )
  }

  return (
    <section className="survey-layout-editor">
      <div className="table-tools">
        <div>
          <h2>{title}</h2>
          <p>{description}</p>
        </div>
        <button
          className="primary-action"
          type="button"
          disabled={disabled || loading || saving || draftItems.length === 0}
          onClick={() => onSave(draftItems, draftFilters)}
        >
          {saving ? 'Saving...' : saveLabel}
        </button>
      </div>
      {message && <div className="success">{message}</div>}

      <fieldset className={`survey-layout-filters${filterExtra ? ' has-extra' : ''}`}>
        <legend>{filterLegend}</legend>
        {filterExtra}
        <div className="survey-layout-filter-row">
          <p className="survey-layout-filter-label">Public filters</p>
          <div className="survey-layout-filter-options">
            {SURVEY_FILTER_FIELDS.map((filter) => (
              <label key={filter.field}>
                <input
                  checked={draftFilters.includes(filter.field)}
                  type="checkbox"
                  onChange={() => toggleFilter(filter.field)}
                />
                {filter.label}
              </label>
            ))}
          </div>
        </div>
      </fieldset>

      {loading ? (
        <div className="empty-state">Loading layout...</div>
      ) : (
        <ol className={`survey-layout-list${imageControlsEnabled ? ' with-image-controls' : ''}`}>
          {draftItems.map((item, index) => {
            const displaySize = item.image_display_size ?? 'medium'
            const imageFit = item.image_fit ?? 'contain'
            const previewPx = sizePx(displaySize)
            return (
              <li
                key={item.id}
                className={`${draggingId === item.id ? 'is-dragging' : ''}${item.page_break_before && index > 0 ? ' has-page-break' : ''}`}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault()
                  if (draggingId !== null) moveItem(draggingId, item.id)
                  setDraggingId(null)
                }}
              >
                {index > 0 && (
                  <button
                    className={`survey-layout-page-break ${item.page_break_before ? 'is-active' : ''}`}
                    type="button"
                    onClick={() => updateItem(item.id, { page_break_before: !item.page_break_before })}
                  >
                    <span>{item.page_break_before ? 'Page break' : 'Add page break'}</span>
                  </button>
                )}

                <div className="survey-layout-item-row">
                  <span
                    className="survey-layout-handle"
                    draggable
                    onDragStart={(event) => {
                      event.dataTransfer.setData('text/plain', String(item.id))
                      event.dataTransfer.effectAllowed = 'move'
                      setDraggingId(item.id)
                    }}
                    onDragEnd={() => setDraggingId(null)}
                  >
                    Drag
                  </span>

                  {imageControlsEnabled && (
                    <div className="survey-layout-image-controls">
                      <div
                        className="survey-layout-image-preview"
                        style={{ width: previewPx, height: previewPx }}
                        title="Click preview to toggle Contain / Cover"
                        onClick={() =>
                          updateItem(item.id, {
                            image_fit: imageFit === 'contain' ? 'cover' : 'contain',
                          })
                        }
                      >
                        {isImageReference(item.image_url) ? (
                          <img
                            src={item.image_url}
                            alt=""
                            style={{ objectFit: imageFit }}
                          />
                        ) : (
                          <span>No image</span>
                        )}
                        <em>{imageFit === 'contain' ? 'Contain' : 'Cover'}</em>
                      </div>
                      <label className="survey-layout-image-slider">
                        Size
                        <input
                          type="range"
                          min={0}
                          max={IMAGE_SIZE_OPTIONS.length - 1}
                          step={1}
                          value={sizeIndex(displaySize)}
                          onChange={(event) =>
                            updateItem(item.id, {
                              image_display_size: IMAGE_SIZE_OPTIONS[Number(event.target.value)].value,
                            })
                          }
                        />
                        <span>{IMAGE_SIZE_OPTIONS[sizeIndex(displaySize)].label} · {previewPx}px</span>
                      </label>
                    </div>
                  )}

                  <div>
                    <strong>{item.item_name}</strong>
                    <p>{[item.brand, item.channel, item.product_type].filter(Boolean).join(' | ')}</p>
                  </div>

                  {showSurvey && (
                    <label>
                      <input
                        checked={item.in_survey}
                        type="checkbox"
                        onChange={(event) => updateItem(item.id, { in_survey: event.target.checked })}
                      />
                      Survey
                    </label>
                  )}

                  {showBuyBook && (
                    <label>
                      <input
                        checked={item.in_buy_book}
                        type="checkbox"
                        onChange={(event) => updateItem(item.id, { in_buy_book: event.target.checked })}
                      />
                      {includeLabel}
                    </label>
                  )}

                  {showResponseWidth && (
                    <label className="survey-layout-width">
                      Response width
                      <input
                        max={70}
                        min={20}
                        type="range"
                        value={item.response_width}
                        onChange={(event) =>
                          updateItem(item.id, { response_width: Number(event.target.value) })
                        }
                      />
                      <span>{item.response_width}%</span>
                    </label>
                  )}
                </div>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
