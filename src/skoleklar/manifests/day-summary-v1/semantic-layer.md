Input fields:

- `class_level`: the class, such as `0. klasse` or `2. klasse`; set the language
  level from it.
- `week`: ISO week number and year of the plan.
- `general`: the plan's general blocks for the whole week, each with an optional
  `subject` and its `text`. Teachers often write these as a letter to parents.
- `days`: Monday to Friday. Each day has its ISO `date`, the Danish `weekday` and
  its `blocks`. A block's `subject` is null when the teacher gave none; the real
  subject is then often the block's first line.

Output fields:

- `date`: copied from the input day.
- `headline`: a few words naming the day's highlight, or null for an empty day.
- `text`: the child-friendly paragraph, or null for an empty day.
- `general_used`: whether the paragraph uses something from `general`.

A summary describes the plan; it is not a homework list or a reminder system.
The family's homework, bring-items and deadlines are extracted separately, so a
`Husk:` sentence is a friendly nudge, never the only place an obligation lives.
