Input fields:

- `subjects`: the latest message subject per child, each with an `id` and its
  `subject`. A subject may be cut short and end with `…`.

Output fields:

- `id`: copied from the input.
- `topic`: one or two Danish words, or null when hidden.
- `hide`: true when the subject is about one of the hidden categories.
