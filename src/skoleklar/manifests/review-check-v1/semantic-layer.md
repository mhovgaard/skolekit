Input fields:

- `today`: the date of the check.
- `children`: each child's `name` and `class_level` (such as `2. klasse`, or null).
- `items`: the flagged facts. Each has an `id`, `child`, `type`, `title`, `summary`,
  `date`, the earlier step's `review_reason`, `evidence` (short quotes from the
  source), a `source_excerpt` and `same_day`: the other facts for that child and
  date, each with `flagged` true when it is also under review.

Output fields:

- `id`: copied from the input.
- `verdict`: `show`, `drop` or `unclear`.
- `note`: a short Danish note (show) or question (unclear), or null.
- `reason`: one English sentence for the audit.
