# RailFuse competition materials and live demonstration, 2026-10-16 V2

This version connects the three design decisions—plan the paths, validate before publication, and retire DMA ownership before reuse—to actual mechanisms and separately dated evidence. It updates the fork's submission landing page and provides a 9-page Chinese introduction, a 3-page presenter guide, a 4:30 narrated walkthrough, mechanism illustrations, audit-derived plots and a fresh real-RXE process-output capture. The core implementation is unchanged from `39ce2d50fcee00405758f1e2c4f7bd389baccf1c`.

## Start here

- [Project introduction](RailFuse-project-introduction-v2.pdf)
- [4:30 narrated walkthrough](RailFuse-demo-v2.mp4)
- [Step-by-step presenter guide](RailFuse-demo-guide-v2.pdf)
- [Timed original process-output capture](live-demo.cast)
- [Original stdout](live-demo.stdout) and [exit code](live-demo.exit)
- [Live per-case receipts](live/receipts-take1/)
- [Scene actions and narration](video-scenes.json)

The video is an edited walkthrough combining new process-output recordings, generated mechanism illustrations, and explicitly dated historical evidence. It is not a continuous macOS desktop recording. The unedited event stream retains approximately 66 seconds of actual stdout/stderr timing. Video waiting gaps are shortened to at most four seconds, as disclosed on the terminal scenes; complete original waits remain in the event stream.

## Environment prerequisites

Use a Linux client and server with working libibverbs/RXE providers. The recorded fixture has two isolated KVM guests on one host, two independent virtual networks, and virtual disks. The client must have `rxe_c0` and `rxe_c1`, port 1, GID index 1. The server exposes coordinator `http://10.31.0.2:55151`, RDMA listeners `10.31.0.2:55153` and `10.32.0.2:55154`, Redis and the configured storage directories. The recorded server configuration is [retained here](../ffi-lifecycle-20261016/server.toml); adapt its paths and provision the services before running a different deployment. `run_demo.py` intentionally names this dedicated fixture through its top-level constants; adjust them together with the route arguments for another topology.

On the client, check `rdma link show` and `ip -br addr`. On the server, check that the configured process and listening ports exist. These instructions prepare and demonstrate the already configured fixture; they do not automatically provision KVM, bridges, RXE kernel modules, Redis, or storage devices.

## Prepare the recorded client fixture

From a Linux checkout of this branch with the documented project build dependencies:

```bash
make build
bash kv-service/verification/results/competition-demo-20261016-v2/prepare_demo.sh \
  "$PWD" /home/railtest/railfuse-demo-20261016-v2
cd /home/railtest/railfuse-demo-20261016-v2
```

The preparation copies the freshly built FFI and Rust bench, current Python wrapper and probes, the demo driver and recorder, and builds the CQ observer. It extracts the archived old library solely for the explicitly requested isolated corruption negative control. The ordinary Python wrapper rejects that unsafe library; only the negative-control probe bypasses the capability check. Do not use that baseline or the test observer as production dependencies.

Build instructions in the root README remain authoritative for project dependencies. The project requires Python 3.10+; the actual unit/check interpreter and guest versions are preserved in their separate receipts. Binary versions, provider and device details affect reproduction; every live run records the library, executable and script hashes before and after.

## Demonstrate, one step at a time

Each command requires a new output directory. Existing output directories are rejected.

```bash
python3 run_demo.py read --output receipts-read-01
python3 run_demo.py late --output receipts-late-01
python3 run_demo.py put --output receipts-put-01
```

- `read`: writes a deterministic 64 MiB object, performs a full-content FFI GET, then reads the same object through one and two Rust Rails. Check the shared SDK object hash, two 32 MiB rail contributions, effective direct mode, and zero terminal in-flight requests/bytes and reserved registered budget. The object checksum is the project's locked `twox-hash 1.6.3` implementation; the separate SHA-256 payload and prior dependency fixture remain in the evidence.
- `late`: the old library must exit the probe with exact payload corruption. The candidate must preserve the same still-registered sentinel through terminal server response/EOF and then verify same-handle reconnect PUT/GET content. The fixed probe normally waits about 30 seconds for the server terminal state.
- `put`: the test observer suppresses CQ polling until QP destruction, then records actual provider WCs. The genuine old completion must be consumed before error return, followed by three same-handle/same-registration PUT/GET rounds. About 31 seconds is expected. RXE may reuse QPNs; QPN inequality is not a safety condition.

To run and record the full sequence:

```bash
python3 record_terminal.py --cast presenter-run.cast -- \
  python3 -u run_demo.py all --output receipts-all-01
```

The recorder captures process stdout/stderr chunks and their timing in asciicast-v2 form, not desktop pixels or interactive input. `live-demo.cast` is the actual parent-captured equivalent from this session; its contents agree with `live-events.json` and `live-demo.stdout`. Only the displayed video excerpts remove transport notices and shorten idle waits; original evidence remains unedited.

## Evidence dates and boundaries

- October 9: archived same-plan Direct/packed-contiguous comparisons. Ten process blocks per size, paired geometric means 1.100x and 1.161x; CPU/RSS values and intervals have their original units. The two sizes have different server source conditions and are compared within size.
- October 12: archived 20-read/15-overwrite Generation witness. Ten accepted results, eight observed Generation-transition rejections and two descriptor-stale rejections. This does not locate exact server commit or in-flight WRITE times.
- October 16 frozen acceptance: [Python 165 passed/11 skipped, native 13, receipt 6 and the full two-link RXE matrix](../ffi-lifecycle-20261016/).
- October 16 V2 live demonstration: one new 64 MiB read/write sequence and single-link baseline/fixed late-WRITE plus controlled-CQ PUT examples. It does not relabel the complete frozen matrix as newly rerun. `source-map.json` binds the archived inputs; `live/receipts-take1/launch.json` and `completed.json` bind current live executables/probes before and after.

Rust RailReader's failure-atomic publication and the Python single-connection FFI's partial-data/lifetime semantics are separate. The scope is real Verbs/RXE execution on two guests, not physical dual-HCA aggregation, a universal correctness proof, or an end-to-end inference speedup claim. The current running server is identified before/after; it was not rebuilt or replaced for the material update.

## Artifact generation and publication

`build_figures.py`, `build_materials.py` and `render_video.py` preserve the generating logic. PDF/chart generation needs ReportLab, Pillow and Matplotlib; the macOS video renderer uses the named fonts, `say` Tingting voice, and ffmpeg. These are artifact-generation dependencies, not ContextStore runtime dependencies. Per-scene audio is measured and must fit without truncation. Generated mechanism images and their original/edit prompts are under `assets`; numerical plots use audit JSON, not image generation.

`SHA256SUMS` inventories these materials. Independent artifact review and visual/decode checks are preserved separately. Source publication, attachment preparation, official email submission and organizer acknowledgment remain separate states.
