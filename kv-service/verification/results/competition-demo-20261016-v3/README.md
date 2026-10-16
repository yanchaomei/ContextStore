# RailFuse competition materials V3 / 2026-10-16

V3 expands the project introduction and replaces all eight narration segments with the user-selected Mandarin neural female voice. The runtime source and the V2 real RXE capture are unchanged.

- [Detailed project introduction, 27 pages](RailFuse-project-introduction-v3.pdf): 20 technical chapters, 6 appendices, 8 technical tables, 4 figures and 32 source/evidence references. The linked contents page and PDF bookmarks support targeted review.
- [Narrated demonstration, 4:30 / 1080p](RailFuse-demo-v3.mp4): clear female neural narration, real captured terminal output, mechanism illustrations and explicitly dated archived experiments.
- [Presenter guide, 3 pages](RailFuse-demo-guide-v3.pdf) and [complete action/narration script](演示逐步操作与完整讲稿.txt).
- [Optional Mandarin sentence subtitles](RailFuse-demo-v3.zh-CN.srt), [captured narration](narration/) and [voice research/selection record](voice-research.md).
- [Independent content review](independent-v3-review.md), [final artifact QA](final-qa-v3.json), [visual QA](visual-qa/) and [source map](source-map-v3.json).
- [Unedited timed terminal capture and live RXE receipts](../competition-demo-20261016-v2/), including `live-demo.cast`, `live-demo.stdout`, `live-events.json` and `live/receipts-take1/`.
- [Frozen FFI/Python safety acceptance](../ffi-lifecycle-20261016/), including the old-library exact-payload corruption negative controls and all candidate late-WRITE / same-registration PUT recovery receipts.

## What the expanded report adds

The detailed report explains descriptor and Placement validation, route discovery and uniqueness, weighted whole-stripe planning, direct-staging eligibility and fallback, SGE/dummy mapping, per-Reader resource formulas, publication/cancellation, source and destination memory retirement, Python/C ABI contracts, failure classification, test layers, late-WRITE witness timing, controlled CQ suppression and recovery, corrected single-rail negative results, statistical methods, Generation transitions and deployment limits. Appendices provide Linux prerequisites, the recorded fixture configuration, Rust/Python interface examples and reproduction/audit entry points.

Independent review found and corrected two documentation issues: successful Rust reads must consume the returned valid prefix rather than assume capacity equals length; clean workspace builds require an explicit `CONTEXTSTORE_RDMA_LIB` path for the Python example. The Rust example remains explanatory and was not newly compiled or run.

## Media provenance and scope

The soundtrack uses `zh-CN-XiaoxiaoNeural` through the Microsoft Edge online endpoint using an isolated `edge-tts==7.3.1` client. Captured MP3s and sentence boundaries are included so ordinary playback is offline. All eight segments fit the scene at rate `-4%`; none was truncated or accelerated. Final encoding applies loudness normalization only. This is not a MiniMax, Doubao, Fish or paid Azure API recording; those candidates are distinguished in the research record.

The video reuses the verified October 16 V2 terminal capture without changing event text/order. Long waits are shortened and labeled in the video; the full raw event stream is preserved. Performance is the October 9 same-plan RXE comparison and version evidence is the October 12 Generation witness. These are separate experiments. There is no new performance campaign or physical dual-HCA/end-to-end inference gain claim in V3. Python's external-buffer FFI remains separate from Rust's failure-atomic publication.

## Rebuild the materials

Use a local document/media environment with Python 3.10+, ReportLab, Pillow, FFmpeg and optional `edge-tts==7.3.1`. These are material-production tools, not runtime dependency changes.

From the repository root:

```sh
MATERIALS=kv-service/verification/results/competition-demo-20261016-v3
EVIDENCE=kv-service/verification/results/competition-demo-20261016-v2
python3 "$MATERIALS/build_report_v3.py" --root "$MATERIALS" --evidence-root "$EVIDENCE"
python3 "$MATERIALS/build_guide_v3.py" --root "$MATERIALS"
python3 "$MATERIALS/render_video_v3.py" --root "$MATERIALS" --evidence-root "$EVIDENCE"
```

The video renderer uses the included MP3s. Optional re-synthesis needs the online service and may produce different bytes in the future:

```sh
python3 "$MATERIALS/generate_narration.py" --root "$MATERIALS" --evidence-root "$EVIDENCE"
```

To rerun the real data-plane demonstration, follow the V2 `prepare_demo.sh` / README and use a new receipt directory. Document rebuilding is separate from core build/RXE validation. SHA256SUMS identifies the delivered material bytes. Branch publication and organizer receipt are separate states; these files do not claim an email was sent or accepted.
