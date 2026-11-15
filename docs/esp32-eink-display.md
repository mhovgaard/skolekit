# E-ink display format

For maintainers implementing a renderer or display client. For parts, pinout
and first setup, use [the board build guide](board-build-guide.md).

The supported target is the Waveshare 7.3-inch e-Paper HAT (E), 800×480
Spectra 6, driven by the FireBeetle 2 ESP32-E. The firmware is not an ESP32-C6
or Raspberry Pi driver. Keep the documented hardware pinout and board target.

## Pixel contract

Every pixel in the indexed PNG is one of these controller codes:

| Colour | Code | Nominal RGB | Monitor approximation |
| --- | --- | --- | --- |
| Black | `0x0` | `#000000` | `#191E21` |
| White | `0x1` | `#FFFFFF` | `#E8E8E8` |
| Yellow | `0x2` | `#FFFF00` | `#EFDE44` |
| Red | `0x3` | `#FF0000` | `#B21318` |
| Blue | `0x5` | `#0000FF` | `#2157BA` |
| Green | `0x6` | `#00FF00` | `#125F20` |

Code `0x4` is unused. The PNG is 800×480, mode `P`, without alpha. Native
`spectra6-4bpp-high-nibble-first` data is exactly 192,000 bytes: two pixels per
byte, left pixel in the high nibble, row-major from top left. This matches the
panel driver's display input. Validate code membership before packing.

The nominal PNG is the device contract. A monitor appearance preview remaps
palette colours for inspection; never pack that remapped image as device data.
Text and structural UI elements use deterministic palette choices. Artwork is
resized first and diffused separately so it cannot bleed into text.

## Transport and draw

The [display API](delivery.md#refresh-and-display) provides metadata, checksum,
expiry and versioned native/PNG endpoints. A display-only device checks these,
streams native bytes to a pending file, validates them, then independently
reads flash back before committing the cache and drawing.
A partial download or failed checksum cannot become a ready frame. See
[frame cache](firebeetle-frame-cache.md).

The panel driver streams cached payload rather than allocating a full RGB
framebuffer. A physical refresh takes substantially longer than fetching
metadata; status must distinguish saved, drawn and failed states. The firmware
posts device status after a sync, but does not currently submit a separate
physical-draw receipt.

## Verification

Run synthetic renderer/contract tests and inspect an 800×480 preview for cut
off obligations and cancellation markers. On a new build, display six colour
bands before a real dashboard and verify rotation, nibble order and palette.
A successful native test or PNG validation does not establish SPI timing,
battery behavior or a physical draw; those need the builder's device checks.
