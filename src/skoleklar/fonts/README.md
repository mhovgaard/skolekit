# Bundled fonts

Newsreader 5.3.0 (Production Type), Latin subset, weights 400 and 700, from the
`@fontsource/newsreader` npm package. Licensed under the SIL Open Font License 1.1
(`OFL-Newsreader.txt`). Embedded as base64 `@font-face` in the dashboard HTML so the
page renders identically on macOS, in the cloud image and on devices.

Arimo 5.3.0 (The Arimo Project Authors), Latin subset, weights 400 and 700, from the
`@fontsource/arimo` npm package. Licensed under the SIL Open Font License 1.1
(`OFL-Arimo.txt`). The panel renderer draws all sans text with it, so a Mac, the
cloud image and GitHub's test machine fit the same text in the same space. Arimo has
Arial's widths, matching the HTML's `Helvetica Neue, Helvetica, Arial` stack. Before
it was bundled, the cloud image drew with the wider DejaVu Sans and dropped text a Mac
fitted.
