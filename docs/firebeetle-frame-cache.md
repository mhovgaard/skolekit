# FireBeetle frame cache

For maintainers changing frame download, flash storage or physical drawing.
Use [board setup](board-build-guide.md) for provisioning and
[display format](esp32-eink-display.md) for the native byte contract.

## Validation path

1. Read authenticated `/v1/display/status` metadata and check device identity,
   version, times/expiry, dimensions, format, length and SHA-256.
2. Request that exact `/v1/display/frame?version=...`. A 409 means the version
   is no longer available; it is not permission to substitute unchecked bytes.
3. Stream through the bounded buffer to a pending flash file while checking
   colour codes, byte count and SHA-256. Incomplete, oversized, expired or
   mismatched data is rejected.
4. Close network TLS and the file, then reopen flash and read it independently.
   Recompute checksum and format checks. Only a successful read-back permits
   cache replacement and ready status.
5. Publish the pending cache by rename. A failed replacement removes pending
   data and preserves the previous verified cache.

The current implementation streams in 16,000-byte pieces; a native frame is
192,000 bytes. Download and read timeouts are bounded in firmware. Preserve
those bounds when changing networking code.

## Flash and restart

Use the No OTA partition scheme documented in the [firmware guide](../firmware/README.md).
Credentials live separately in NVS. The frame cache carries a versioned metadata
header before its native payload; a panel driver must skip that header.
Keep space for pending and current files without a full RAM framebuffer.

On boot, remove interrupted pending files and verify the current file locally
once a valid clock is available. An expired cache is not ready. Reclaiming a
flash file is not a guarantee of secure physical erasure.
A local read-back contacts no cloud service and is distinct from a sync.

## Diagnostics and tests

```sh
.venv/bin/python firmware/device.py frame-test
.venv/bin/python firmware/device.py frame-readback
.venv/bin/python firmware/device.py status --details
```

`sync` is the normal download/verify/draw operation. During it the web status
may use a smaller progress document to reserve memory for concurrent TLS.
The maintenance page and USB share readable operation/result rows; completed
sync statistics remain separate from local cache-check statistics.

Test truncation, overflow, checksum/palette errors, expiry, interrupted writes,
restart recovery and preservation of the previous good file. Then verify one
real download, independent read-back and draw on the target board. Keep
checksums, serial logs and project-specific evidence in a private build record.
