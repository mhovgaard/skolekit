# E-ink interface concepts

Historical design studies for maintainers exploring layout alternatives. These
synthetic images are not the production renderer or build instructions. Use
[board generation](../../docs/board-generation.md) and the
[rendering workflows](../../rendering/README.md) for the current implementation.


Three 800 x 480 interface studies for an early 7.3-inch e-ink layout study. They use
synthetic data and are rendered entirely with Pillow.

Render all concepts without changing the project's dependency lockfile:

```bash
uv run --with pillow python prototypes/eink/render_concepts.py
```

The PNGs are written to `prototypes/eink/output/`.

## Design constraints

- The two-child weekday grid is the primary information surface.
- Colour communicates meaning: blue is schoolwork, green is an event, yellow is
  something to bring or prepare, and red/orange is reserved for attention.
- The display has no live clock. It can therefore update only when the content or
  freshness state changes.
- Each child/day cell supports three compact homework/action rows plus one event
  row. Per-child events live in that child's band, with category dots making the
  distinction visible.
- Private-message sender metadata uses the restricted `First I.` participant form,
  role, recency, and unread state. Subjects and previews remain excluded from the
  ambient household screen.
- The palette is intentionally restrained. Actual panel output must be checked on
  hardware because an RGB concept does not establish Spectra 6 palette fidelity.

## Concepts

1. **Weekly grid** — the strongest default: both children, five days, shared
   notices, and a compact message board.
2. **Today first** — gives today and tomorrow more room while retaining the rest
   of the week as a narrow horizon.
3. **Quiet week** — maximum calm: one line per child/day and a single attention
   card. This is most suitable when the source pipeline is already very selective.
