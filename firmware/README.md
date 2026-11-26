# FireBeetle firmware

For builders flashing the board and maintainers changing device code. Use the
[board build guide](../docs/board-build-guide.md) for the complete parts,
cloud registration, wiring, certificate and calibration workflow.

The supported controller is **DFRobot FireBeetle 2 ESP32-E with headers
(DFR0654-F)**, ESP32-WROOM-32E with 4 MB flash. It is not the FireBeetle C6.
The panel is the Waveshare 7.3-inch HAT (E), 800×480 Spectra 6.
`firebeetle-cloud/cloud_app.cpp` declares the current source firmware version
(`ag-cloud-0.8.0`); this does not identify the build on a particular device.

## Sketches and tools

| Path | Purpose |
| --- | --- |
| `firebeetle-cloud/` | Production cloud client, cache, panel, status and sleep policy |
| `firebeetle-smoke/` | Bare-board LED/serial diagnostic before connecting the panel |
| `firebeetle-epd-test/` | Standalone panel bands/test patterns without cloud credentials |
| `firebeetle-wifi/` | Isolated Wi-Fi playground, not a production provisioning path |
| `device.py` | Private registration, USB provisioning and operational helper |

The build wrapper keeps toolchains in ignored `.toolchain/`. The documented
baseline uses Arduino CLI 1.5.1 and Espressif Arduino core 3.3.12, with target
`esp32:esp32:dfrobot_firebeetle2_esp32e`. Put Arduino CLI at
`.toolchain/bin/arduino-cli`, then run from the repository root:

```sh
sh firmware/arduino.sh core update-index
sh firmware/arduino.sh core install esp32:esp32@3.3.12
sh firmware/arduino.sh board list
```

Upgrading toolchains is separate work: compile, run native tests and perform
hardware acceptance rather than assuming an unrelated board example applies.

## Build and flash

Use the **No OTA** partition scheme for the app and frame cache:

```sh
sh firmware/arduino.sh compile   --fqbn esp32:esp32:dfrobot_firebeetle2_esp32e --board-options PartitionScheme=no_ota   --build-path "$PWD/firmware/.toolchain/build/firebeetle-cloud" firmware/firebeetle-cloud
sh firmware/arduino.sh upload   --fqbn esp32:esp32:dfrobot_firebeetle2_esp32e --board-options PartitionScheme=no_ota,UploadSpeed=230400   --port /dev/cu.wchusbserialNN   --input-dir "$PWD/firmware/.toolchain/build/firebeetle-cloud" firmware/firebeetle-cloud
```

Replace the serial port with your own. A CH340 driver and data-capable USB
cable are needed. Close monitors before uploading; one process owns the port.
The monitor uses 115200 baud. Uploading replaces the installed program; OTA
is not implemented. Provision secrets through `device.py`, never in source or
command-line secret arguments. The [build guide](../docs/board-build-guide.md)
contains the authoritative pin mapping and hardware checks.

## Cache, draw and status

The device reads authenticated status and a versioned frame from its configured
HTTPS gateway. Its display-only token cannot request school-data writes.
It validates native length, palette, expiry and SHA-256, independently reads
flash back, then draws. Failed replacements preserve a previous good cache.
See [frame cache](../docs/firebeetle-frame-cache.md) and
[display format](../docs/esp32-eink-display.md).

The local page at `https://firebeetle-ag.local/` uses per-device TLS and an admin
password. After each sync, the latest device report goes to the control bucket
for the browser's `/board-status` page. A failed report is separate from the
draw result. The report contains the prior report's result because it is
assembled before the current send completes. A low-memory send can be skipped;
do not infer that a missing report means a frame was not drawn.

```sh
.venv/bin/python firmware/device.py status --details
.venv/bin/python firmware/device.py sync
.venv/bin/python firmware/device.py frame-readback
.venv/bin/python firmware/device.py watch --minutes 20
```

## Power and wake policy

| Mode | Behavior |
| --- | --- |
| `manual` | Explicit sync only; a verified cached frame may be drawn at boot |
| `awake` | Stay awake and check publications at scheduled wake times |
| `sleep` | Wake, sync/report, then deep-sleep; maintenance/button wakes keep the page available temporarily |

The default scan-following wake is 06:00 every day, after the 05:30 scan; a
frame transition within 20 minutes of it takes its place, so a school morning is
one wake. The board also wakes 30 seconds after each published frame transition. Retry/backoff and the
maximum sleep bound also apply. Changing cloud scan times requires checking
this device policy; a cloud schedule does not reconfigure firmware wake constants.
Below 3.45 V, the device skips Wi-Fi/refresh and sleeps 12 hours, preserving
the last picture. GPIO27 is the user button; it is not the boot button.

The current timer policy learns drift only from suitable timer wakes with
trusted clock synchronization, then adjusts requested sleep time toward the
planned wall-clock wake. Status fields include `timer_drift_ppm`, samples and
requested timer duration. Validate this across multiple cycles after an update.
Timer drift correction is separate from battery voltage calibration.

## Validation and limits

Run the native C++ checks exercised by `tests_python/test_firebeetle_tools.py`,
compile the intended sketch, then validate on the actual hardware: six colour
bands, authenticated download, checksum/read-back, physical draw, reboot recovery,
button/timer wakes and status reporting. Use short wake-test cycles before the
normal schedule. Preserve production credentials while testing.

Calibrate voltage against a multimeter on the cell. USB-only and charging
readings can mislead, and USB-only timer wakes can brown out. Sleep current,
HAT idle draw and battery endurance must be measured for the assembled device.
There is no enclosure design, encrypted credential store, OTA update or physical
draw receipt in this firmware. Display credentials can be revoked per device.

The panel register sequence is adapted from Waveshare under the
[panel MIT notice](firebeetle-cloud/PANEL-LICENSE.txt); keep that notice.
