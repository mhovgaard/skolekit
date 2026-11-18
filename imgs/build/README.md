# Build-guide visuals

Four editorial assembly images made with the built-in image generation tool,
using the author's four supplied build photographs as edit targets. The first
image also uses `../readme/parents-in-situ.png` as a style reference; the other
three use `01-parts-overview.png` to keep photography and typography consistent.
No originals are copied into the public repository.

| File | Original edit target | Purpose and overlays |
| --- | --- | --- |
| `01-parts-overview.png` | IMG_6201.jpeg | Parts overview: e-paper panel; FireBeetle 2 ESP32-E; display HAT and ribbon; 3.7 V LiPo; picture frame and mount. |
| `02-controller-connections.png` | IMG_6202.jpeg | Controller close-up: FireBeetle; PH2.0 battery connector; jumper-wire harness; Waveshare display HAT. |
| `03-frame-dry-fit.png` | IMG_6203.jpeg | Open frame: LiPo; FireBeetle; HAT; loose ribbon bend. Explicitly labelled as a dry fit. |
| `04-connection-details.png` | IMG_6204.jpeg | Macro detail: jumper connectors; exposed contacts; display ribbon. |

The parts overview appears in the root README. All four appear in
[the build guide](../../docs/board-build-guide.md), with plain-text descriptions
and the authoritative wiring table. These are AI-assisted editorial
interpretations, not documentary proof of exact pin positions or a validated
enclosure. Colours are not a signal map. Battery protection, capacity and
polarity cannot be established from the photographs. The foam and loose boards
in the rear view show a prototype dry fit; permanent mounting, insulation and
clearance must be provided by the builder.

The visual treatment follows the README home images: realistic materials,
soft daylight and subtle refinement. Clutter and reflections are removed;
charcoal headlines and fine leader lines identify the components. No dashboard
content, people or household information are added. Label text stays short;
firmware setup, full pin mapping and test requirements remain in Markdown.

## Final prompt set

Each prompt below was sent as a separate built-in image generation request,
with `transparent_background=false`. Image 1 is always the original edit
target; Image 2 is the supporting style image described above.

### 01 · Parts overview

```text
Use case: style-transfer.
Asset type: annotated editorial assembly photograph for Skolekit's DIY e-ink build guide, image 01 of a four-image series.
Input images: Image 1 (IMG_6201.jpeg) is the EDIT TARGET showing the real build laid out on a wooden table. Image 2 (parents-in-situ.png) is STYLE REFERENCE ONLY for restrained photographic realism, walnut wood, cream mount and soft natural light; do not copy its screen or setting.
Primary request: clean and art-direct the supplied build photograph into a beautiful highly realistic, very lightly stylized editorial flat lay with accurate component-name overlays. Keep this recognizably the author's actual hardware, not a generic render. Wide landscape 3:2 composition, high resolution. Retain the walnut picture frame, real 7.3-inch bare display seen from the rear, its small blue adapter and white ribbon to the separate blue Waveshare driver HAT, black FireBeetle board with headers, silver LiPo and coloured jumper harness. All components fully within image. Rear display remains blank black, no dashboard. Preserve each real part's identity, scale, connector shape and connection sequence; carefully spread the layout so each part is legible. Remove pencil, sharpener, eraser, loose knife blade, red tools, packaging scraps, barcode sticker, household reflections and cutting board. Keep a pale warm oak tabletop with subtle grain and soft daylight shadows, neutral white balance, reduced yellow cast. No added objects or circuit boards.
Overlay style: refined practical maker-magazine spread, restrained charcoal serif headline, clean readable sans-serif labels, thin charcoal leader lines ending in small dots on the correct parts; generous whitespace, no opaque cards over hardware, no colourful graphic badges. A small section marker and title at upper left: "01 / THE PARTS" then "A screen, a controller, a frame". Exactly five callouts, with second line smaller:
"7.3-inch e-paper panel" / "Waveshare Spectra 6 · 800 × 480" pointing to the large rear display;
"FireBeetle 2 ESP32-E" / "DFR0654-F · with headers" pointing to black microcontroller;
"Display HAT + ribbon" / "Driver board and panel adapter" pointing to the separate large blue HAT, with the small blue panel adapter and ribbon visibly connected;
"3.7 V LiPo battery" / "Check plug polarity before connecting" pointing to silver pouch;
"Picture frame + mount" / "Choose depth for the complete build" pointing to walnut frame.
Treat each quoted string as verbatim text. No other overlay text. Do not invent battery capacity or claim hidden protection hardware. Do not turn illustration into a technical pin diagram. Avoid glossy CGI, painterly treatment, glow, floating explosion, extra hardware or logos. Match the style reference's tactile realism with only subtle editorial cleanup.
```

### 02 · Controller connections

```text
undefined
```

### 03 · Frame dry fit

```text
undefined
```

### 04 · Connection details

```text
undefined
```
