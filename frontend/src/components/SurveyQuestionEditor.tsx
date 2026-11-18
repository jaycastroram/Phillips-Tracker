import type { SurveyQuestion, SurveyQuestionType } from '../types'

type Props = {
  questions: SurveyQuestion[]
  disabled?: boolean
  onChange: (questions: SurveyQuestion[]) => void
  note?: string
}

const TYPE_OPTIONS: { value: SurveyQuestionType; label: string }[] = [
  { value: 'yes_no', label: 'Yes / No' },
  { value: 'single_choice', label: 'Single choice (radio)' },
  { value: 'dropdown', label: 'Dropdown' },
  { value: 'checkbox', label: 'Checkboxes (multi)' },
  { value: 'text', label: 'Text' },
  { value: 'scale', label: 'Scale 1–5' },
]

const CHOICE_TYPES: SurveyQuestionType[] = ['single_choice', 'dropdown', 'checkbox']

function newQuestion(type: SurveyQuestionType = 'yes_no'): SurveyQuestion {
  const id = `q_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`
  if (type === 'text') {
    return { id, type, label: 'Additional notes', required: false, options: [] }
  }
  if (type === 'scale') {
    return { id, type, label: 'How effective is this item?', required: false, options: [] }
  }
  if (type === 'single_choice' || type === 'dropdown') {
    return {
      id,
      type,
      label: type === 'dropdown' ? 'Choose one from the list' : 'Choose one option',
      required: true,
      options: ['Option A', 'Option B'],
    }
  }
  if (type === 'checkbox') {
    return {
      id,
      type,
      label: 'Choose all that apply',
      required: false,
      options: ['Option A', 'Option B', 'Option C'],
    }
  }
  return {
    id,
    type: 'yes_no',
    label: 'Are you interested in this item?',
    required: true,
    options: ['Yes', 'No'],
  }
}

export function SurveyQuestionEditor({ questions, disabled = false, onChange, note }: Props) {
  function updateQuestion(index: number, updates: Partial<SurveyQuestion>) {
    onChange(
      questions.map((question, questionIndex) =>
        questionIndex === index ? { ...question, ...updates } : question,
      ),
    )
  }

  function moveQuestion(index: number, direction: -1 | 1) {
    const target = index + direction
    if (target < 0 || target >= questions.length) return
    const next = [...questions]
    const [moved] = next.splice(index, 1)
    next.splice(target, 0, moved)
    onChange(next)
  }

  function addQuestion() {
    onChange([...questions, newQuestion()])
  }

  return (
    <div className="survey-question-block">
      <div className="survey-question-head">
        <div>
          <h3>Questions</h3>
          <p className="survey-section-note">
            {note ?? 'Each respondent answers these for every selected item, in this order.'}
          </p>
        </div>
        <span className="survey-question-count">
          {questions.length} {questions.length === 1 ? 'question' : 'questions'}
        </span>
      </div>

      <ol className="survey-question-list">
        {questions.map((question, index) => {
          const isChoice = question.type === 'yes_no' || CHOICE_TYPES.includes(question.type)
          return (
            <li key={question.id} className="survey-question-row">
              <div className="survey-question-row-top">
                <span className="survey-question-handle" aria-hidden="true">
                  {index + 1}
                </span>
                <div className="survey-question-moves">
                  <button
                    aria-label={`Move question ${index + 1} up`}
                    disabled={disabled || index === 0}
                    type="button"
                    onClick={() => moveQuestion(index, -1)}
                  >
                    ↑
                  </button>
                  <button
                    aria-label={`Move question ${index + 1} down`}
                    disabled={disabled || index === questions.length - 1}
                    type="button"
                    onClick={() => moveQuestion(index, 1)}
                  >
                    ↓
                  </button>
                </div>

                <label className="survey-question-type">
                  <span className="survey-sr-only">Answer type</span>
                  <select
                    disabled={disabled}
                    value={question.type}
                    onChange={(event) => {
                      const type = event.target.value as SurveyQuestionType
                      updateQuestion(index, {
                        type,
                        options:
                          type === 'yes_no'
                            ? ['Yes', 'No']
                            : CHOICE_TYPES.includes(type)
                              ? // A yes/no question's Yes/No pair is meaningless for a
                                // multi-choice question, so give it generic options instead.
                                question.type === 'yes_no' || question.options.length < 2
                                ? type === 'checkbox'
                                  ? ['Option A', 'Option B', 'Option C']
                                  : ['Option A', 'Option B']
                                : question.options
                              : [],
                      })
                    }}
                  >
                    {TYPE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="survey-question-required">
                  <input
                    checked={question.required}
                    disabled={disabled}
                    type="checkbox"
                    onChange={(event) => updateQuestion(index, { required: event.target.checked })}
                  />
                  Required
                </label>

                <button
                  aria-label={`Remove question ${index + 1}`}
                  className="survey-question-remove"
                  disabled={disabled || questions.length <= 1}
                  type="button"
                  onClick={() => onChange(questions.filter((_, questionIndex) => questionIndex !== index))}
                >
                  ✕
                </button>
              </div>

              <label className="survey-question-text">
                <span className="survey-sr-only">Question</span>
                <input
                  disabled={disabled}
                  placeholder="Question text"
                  value={question.label}
                  onChange={(event) => updateQuestion(index, { label: event.target.value })}
                />
              </label>

              {isChoice && (
                <div className="survey-question-options">
                  <span className="survey-field-label">Answer options</span>
                  <ul>
                    {question.options.map((option, optionIndex) => (
                      <li key={`${question.id}-${optionIndex}`}>
                        <input
                          aria-label={`Option ${optionIndex + 1} for question ${index + 1}`}
                          disabled={disabled}
                          value={option}
                          onChange={(event) => {
                            const next = [...question.options]
                            next[optionIndex] = event.target.value
                            updateQuestion(index, { options: next })
                          }}
                        />
                        <button
                          aria-label={`Remove option ${optionIndex + 1} from question ${index + 1}`}
                          disabled={disabled || question.options.length <= 1}
                          type="button"
                          onClick={() =>
                            updateQuestion(index, {
                              options: question.options.filter((_, optionIdx) => optionIdx !== optionIndex),
                            })
                          }
                        >
                          ✕
                        </button>
                      </li>
                    ))}
                  </ul>
                  <button
                    className="survey-question-add-option"
                    disabled={disabled}
                    type="button"
                    onClick={() =>
                      updateQuestion(index, { options: [...question.options, `Option ${question.options.length + 1}`] })
                    }
                  >
                    + Add option
                  </button>
                </div>
              )}
            </li>
          )
        })}
      </ol>

      <button className="survey-question-add" disabled={disabled} type="button" onClick={addQuestion}>
        + Add question
      </button>
    </div>
  )
}
