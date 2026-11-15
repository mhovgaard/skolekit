# Weather-dependent weekend house artwork

For builders replacing default art and maintainers changing selection or
packaging. Image authoring is offline; scans only load the reviewed assets.

The live HTML dashboard and the Spectra 6 frame show the house illustration in
the weekend cover. Both select the same preset from the **first displayed
forecast day**: on Friday that is Saturday, and after Saturday's sunset it is
Sunday. Selection uses the existing 09:00–16:00 cover icon, so evening rain does
not replace a sunny daytime illustration.

March–May use spring, June–August summer, September–November autumn, and
December–February winter. Rain and storm icons select the rainy scene; other
non-snow icons select the dry scene. A snow forecast selects the snow scene in
any season. The images illustrate the forecast; the adjacent weather figures
retain the precise conditions, precipitation and warnings.

Nine cached assets live in `src/skoleklar/artwork/`. They are included in the
Python package, Cloud Build upload and both Docker images through `COPY src`.
HTML embeds one WebP as an image data URI, keeping the page self-contained.
The browser CSP permits data URIs for images and fonts only. No image generation
or extra forecast request occurs during rendering.

For e-ink, the selected illustration is resized to its final 340×200 region and
converted with six-pigment serpentine Floyd–Steinberg diffusion. It is pasted
after the dashboard's text-protecting quantization, so artwork colours cannot
bleed into text or the forecast. The resulting PNG and packed frame retain the
existing hardware codes and validation.

## The house

The default house is an AI-generated fictional Danish murermestervilla, with
red brick, a tiled roof, a green door, a birch and an apple tree. It is nobody's
home. The nine masters live in `imgs/house/`; `summer-dry.png` is the reference
for the other eight. All were generated with the built-in image generation
tool on 15 November 2026, using seasonal/weather edits of that reference.
The exact prompts and provenance are in `imgs/house/generation.json`.

Review new masters for architectural consistency and legibility at 340×200,
then package them from the repository root:

```sh
uv run --extra board python -m skoleklar.artwork_build
```

The packer validates all nine source images and prompt entries before writing
680×400 WebPs. It records the source SHA-256 hashes and crop settings in
`src/skoleklar/artwork/sources.json`. Generation is an authoring step; scans
and renderers never call an image model. New images reach production after
building and deploying a new application image and running a scan.

## A household's own house

A household can show its own house instead, which is much of the charm of the
board. Its pictures are private, like its other files, and never part of the
repository:

- put `artwork/<preset>.webp` in the household's folder
  (`~/.config/ag-skoleintra/`), one file for each preset it wants to replace:
  `spring-dry`, `spring-rain`, `summer-dry`, `summer-rain`, `autumn-dry`,
  `autumn-rain`, `winter-dry`, `winter-rain`, `winter-snow`;
- each is a WebP of 680×400, at most 2 MB. A file of another kind or size is
  passed over and the packaged house is shown, so a bad file never breaks the
  board;
- a preset without a file of its own shows the packaged house.

A local scan reads them from that folder. The cloud scan reads them from the
private bucket, beside the household's other files, and never uploads or
changes them:

```sh
gcloud storage cp ~/.config/ag-skoleintra/artwork/*.webp gs://PROJECT-ag-private-state/state/artwork/
```

The next scan draws the board with them. Only the scan needs them: the browser
board and the e-ink board show what the scan drew.

Opening the dashboard still reads the current publication; the owner refresh
action selects new artwork with the next forecast publication.
