# README visuals

The README uses three illustrative home scenes and three exact dashboard previews.
All school content, child names and events are synthetic.

- `parents-board.png`, `kids-board.png` and `weekend-board.png` are 800×480
  monitor previews from
  the application's actual panel renderer, using its `PANEL_APPEARANCE_RGB`
  palette. All three source panel images passed `validate_panel_image` and packed
  to 192,000 bytes. They are previews, not device-ready nominal-palette PNGs.
- The example week starts Monday 9 November 2026. Alfa and Beta are invented.
  The parents' example derives from `heavy_week_state` in
  `tests_python/test_parents_board.py`; the children's example uses the
  same week with invented lessons through `build_day_view`. Artwork comes
  from the packaged fictional house. Generation ran with an empty private
  household directory, so no personal artwork, school session or data was read.
- `parents-in-situ.png` and `kids-in-situ.png` were generated with the built-in
  image generation tool. Two author-supplied photographs informed the frame
  and placement. The matching exact panel preview was the third reference.
  These are illustrative composites, not proof of a particular hardware build.
- `weekend-board.png` uses synthetic weather for 14–15 November 2026, built
  with `tests_python/weather_fixtures.py` and the application weather rules.
  Saturday has a 10–14 dry trip window; Sunday has 10–13. Sunrise (07:45),
  sunset (16:10), forecasts and activities are invented example data.
  Rendering used an empty private household directory and the packaged autumn
  house, without reading a school session, household records or personal art.
- `weekend-in-situ.png` was generated with the built-in image generation tool,
  using the existing parents' and children's scenes as style references and the
  exact weekend panel preview as its screen reference. It is an illustrative
  composite, not a photograph of the device running this example.
- Original photographs and real household screenshots are not included.
  Screen colours approximate reflective e-ink on a monitor; ambient light and
  the physical panel affect their appearance.

## Final image prompts

### Parents' home scene

```text
Use case: compositing. Asset type: wide README product lifestyle image. Primary request: create a lightly stylized, highly realistic editorial photograph of the actual DIY e-ink dashboard in its home setting, using supplied references. Reference 1: original close-up of real wooden frame. Reference 2: original wider home scene with pinned paper school timetable and wall calendar. Reference 3: EXACT synthetic dashboard content and colours to insert into the frame. Match the real frame's walnut-brown grain, simple cream mount, landscape 5:3 screen and understated scale. Keep composition close to reference 2, a fairly close, near-front-on view of entire frame on warm neutral plaster wall, a partial pinned timetable on left, calendar cropped below. Photographic wood texture and gentle soft daylight, only a very subtle editorial refinement; no painterly texture, gouache, cartoon or watercolour. Remove room reflections and glare enough to read screen. Keep screen almost front-on, matte and non-luminous, a real reflective Waveshare Spectra 6 display. CRITICAL: faithfully reproduce the dashboard image in reference 3 including its composition, all text, labels and real six-colour e-ink look. Display paper is cool pale grey #E8E8E8, dark charcoal #191E21, blue #2157BA and restrained yellow #EFDE44; illustration inside children dashboard may also use muted red and green e-ink pigments and visible dithering. Do not warm-tint the screen to cream or beige; cream mount and screen must be visibly distinct. Do not brighten the panel into an LCD, add glow, gradients, touch buttons or invented interface features. Use invented Alfa/Beta details exclusively from reference 3. Remove original private names/handwriting, brands and calendar photography from the home references; adjacent papers are generic. No people, no headlines or overlay text, no logos. Output wide landscape 3:2 image, product frame large and legible, softly realistic rather than a stylized painting.
This image shows the parents' board: charcoal left column, pale grey right column, two child rows and small blue event chips.
```

### Children's home scene

```text
Use case: compositing. Asset type: wide README product lifestyle image. Primary request: create a lightly stylized, highly realistic editorial photograph of the actual DIY e-ink dashboard in its home setting, using supplied references. Reference 1: original close-up of real wooden frame. Reference 2: original wider home scene with pinned paper school timetable and wall calendar. Reference 3: EXACT synthetic dashboard content and colours to insert into the frame. Match the real frame's walnut-brown grain, simple cream mount, landscape 5:3 screen and understated scale. Keep composition close to reference 2, a fairly close, near-front-on view of entire frame on warm neutral plaster wall, a partial pinned timetable on left, calendar cropped below. Photographic wood texture and gentle soft daylight, only a very subtle editorial refinement; no painterly texture, gouache, cartoon or watercolour. Remove room reflections and glare enough to read screen. Keep screen almost front-on, matte and non-luminous, a real reflective Waveshare Spectra 6 display. CRITICAL: faithfully reproduce the dashboard image in reference 3 including its composition, all text, labels and real six-colour e-ink look. Display paper is cool pale grey #E8E8E8, dark charcoal #191E21, blue #2157BA and restrained yellow #EFDE44; illustration inside children dashboard may also use muted red and green e-ink pigments and visible dithering. Do not warm-tint the screen to cream or beige; cream mount and screen must be visibly distinct. Do not brighten the panel into an LCD, add glow, gradients, touch buttons or invented interface features. Use invented Alfa/Beta details exclusively from reference 3. Remove original private names/handwriting, brands and calendar photography from the home references; adjacent papers are generic. No people, no headlines or overlay text, no logos. Output wide landscape 3:2 image, product frame large and legible, softly realistic rather than a stylized painting.
This image shows the children's day view: pale background, house artwork and reminders at left, Alfa and Beta lesson rows on right.
```


### Weekend home scene

```text
Use case: compositing.
Asset type: wide README product lifestyle image for Skoleklar in the Skolekit project.
Primary request: Create a matching third home scene showing the weekend dashboard, consistent with the existing parents and children's README mockups.
Input images: Image 1 is a reference image for the parents' home scene, wooden frame, cream mount, wall texture and surrounding papers. Image 2 is a reference image for the children's home scene, same product and photographic style. Image 3 is the EXACT synthetic weekend screen to insert into the frame; it is a supporting compositing input and must be faithfully reproduced.
Scene/backdrop: Same warm neutral Scandinavian home wall, walnut-brown landscape picture frame with a cream mount, partial generic paper school timetable pinned to the left and cropped wall calendar below.
Style/medium: Lightly stylized, highly realistic editorial photograph, photographic wood grain and plaster, soft natural daylight, matte reflective e-ink.
Composition/framing: Landscape 3:2, close near-front-on view, complete frame large and legible, screen retains exact 800:480 aspect ratio, match scale and viewpoint of the two existing scenes.
Screen: Replace the schoolwork screen with image 3, preserving its two-column composition and exact labels. Left is WEEKEND, Lørdag 14. nov., the colourful dithered autumn house, Skovtur kl. 11 · medbring kakao, Brætspil med venner kl. 14, Ingen ulæste. Right is WEEKENDVEJR · BYEN, Blandet, LØRDAG and SØNDAG forecasts, and two sunrise/sunset arcs marked TUR 10–14 and TUR 10–13. Preserve those trip-window numbers, weather symbols, temperatures, blue rain figures and yellow/blue arc segments exactly as in image 3. Do not invent homework, lessons, new UI controls, batteries, timestamps, or extra text.
Color palette: Screen paper cool pale grey #E8E8E8, dark charcoal #191E21, blue #2157BA and restrained yellow #EFDE44, muted red/green e-ink pigments in house artwork, visible dithering. Cream mount and cool grey screen must remain distinct. No luminous screen, glare, LCD gradients or warm screen tint.
Constraints: Preserve the quiet DIY physical product. No people, private names, real school data, product logos, marketing headlines or overlay text. Calendar and timetable are incidental and generic. Match the realistic style and physical device in the reference scenes; do not redesign the room or frame.
```
