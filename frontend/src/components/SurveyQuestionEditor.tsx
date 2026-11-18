import type { SurveyQuestion, SurveyQuestionType } from '../types'

type Props = {
  questions: SurveyQuestion[]
  disabled?: boolean
  onChange: (questions: SurveyQuestion[]) => void
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
  const id = `q_${Date.now().toString(36)}`
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

export function SurveyQuestionEditor({ questions, disabled = false, onChange }: Props) {
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

  return (
    <fieldset className="survey-layout-filters has-extra survey-question-editor">
      <legend>Survey questions</legend>
      <p className="survey-layout-filter-note">
        These questions appear on every selected item. Save with the survey layout below.
      </p>

      <div className="survey-question-list">
        {questions.map((question, index) => (
          <div key={question.id} className="survey-question-card">
            <div className="survey-question-card-top">
              <label>
                Type
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
                            ? question.options.length >= 2
                              ? question.options
                              : ['Option A', 'Option B']
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
              <div className="survey-question-actions">
                <button disabled={disabled || index === 0} type="button" onClick={() => moveQuestion(index, -1)}>
                  Up
                </button>
                <button
                  disabled={disabled || index === questions.length - 1}
                  type="button"
                  onClick={() => moveQuestion(index, 1)}
                >
                  Down
                </button>
                <button
                  disabled={disabled || questions.length <= 1}
                  type="button"
                  onClick={() => onChange(questions.filter((_, questionIndex) => questionIndex !== index))}
                >
                  Remove
                </button>
              </div>
            </div>

            <label>
              Question
              <input
                disabled={disabled}
                value={question.label}
                onChange={(event) => updateQuestion(index, { label: event.target.value })}
              />
            </label>

            {(question.type === 'yes_no' || CHOICE_TYPES.includes(question.type)) && (
              <label>
                Options (one per line)
                <textarea
                  disabled={disabled}
                  rows={question.type === 'yes_no' ? 2 : 4}
                  value={question.options.join('\n')}
                  onChange={(event) =>
                    updateQuestion(index, {
                      options: event.target.value
                        .split('\n')
                        .map((line) => line.trim())
                        .filter(Boolean),
                    })
                  }
                />
              </label>
            )}
          </div>
        ))}
      </div>

      <button
        className="secondary-action"
        disabled={disabled}
        type="button"
        onClick={() => onChange([...questions, newQuestion()])}
      >
        Add question
      </button>
    </fieldset>
  )
}
