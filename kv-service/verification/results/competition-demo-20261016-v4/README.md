# RailFuse competition materials V4 / 2026-10-16

V4 adds four AI-generated mechanism illustrations, integrates three into the narrated video, and packages the reusable production workflow as a Codex skill. Runtime sources, raw RXE capture, historical results and the accepted neural narration are unchanged.

- [Detailed introduction, 29 pages / 8 figures](RailFuse-project-introduction-v4.pdf): 20 chapters, 6 appendices, 8 technical tables and 32 pinned evidence/source references.
- [4:30 / 1080p neural female narrated demonstration](RailFuse-demo-v4.mp4), [three-page presenter guide](RailFuse-demo-guide-v4.pdf), [action/narration script](演示逐步操作与完整讲稿.txt), [optional Mandarin subtitles](RailFuse-demo-v4.zh-CN.srt).
- [New original illustrations](assets/image-manifest-v4.json): direct staging/assembly, resource reservations, publication under identity change, and controlled CQ recovery. [Generation/edit prompts](assets/image-generation-prompts-v4.json) and [final CQ wording correction](assets/cq-review-correction.json) are retained. Original PNGs are included; the PDF embeds compact high-quality versions without changing their intended content.
- [Independent image review](independent-image-review.md), [PDF content extraction audit](pdf-content-qa.json), [narration timing audit](narration-audit-v4.json) and [final media QA](final-qa-v4.json).
- [Reusable competition-submission-studio skill](workflow-skill/SKILL.md), with focused references, narration-duration checks and deterministic ZIP/hash verification. [Skill validation records](workflow-skill-validation/) distinguish tool tests from content/visual/listening review.
- [Original V3 content review and voice selection](../competition-demo-20261016-v3/) and [V2 unedited timed capture / live receipts](../competition-demo-20261016-v2/).
- [Frozen FFI/Python safety acceptance and old-library exact-corruption negative controls](../ffi-lifecycle-20261016/).

## New mechanism figures

| Figure | Report | Meaning |
| --- | --- | --- |
| [Staging layout](assets/staging-layout.png) | Chapter 7 | Remove packed-to-ordered assembly while retaining final publication; keep dummy allocations separate from direct data arrows. |
| [Resource lifecycle](assets/budget-lifecycle.png) | Chapter 9 | Release transient/registered/in-flight reservations after transfer; retain final staging and active-read credit until publication/discard. |
| [Publication decision](assets/publication-timeline.png) | Chapter 10 | Publish only a checked complete identity; preserve caller memory when version/layout changes. |
| [CQ recovery](assets/cq-reconnect-timeline.png) | Chapter 16 | Close control stream, destroy old QP, drain real provider WC before the request returns an error, then reconnect using the same registered region. |

All four are explanatory mechanism diagrams, not new empirical measurements. Numerical reservation examples were checked against the implementation. The CQ diagram was corrected after independent review to avoid claiming an explicit IBV_QPS_ERR transition absent from this FFI implementation. Data plots remain derived from archived audit inputs.

## Video and voice

The video adds a staging diagram at 01:35–01:43, a publication diagram at 02:35–02:45, and a CQ recovery diagram at 03:25–03:33. Terminal event contents and ordering are preserved; long waits are shortened and labeled, with the original capture available separately. The soundtrack reuses the exact eight V3 `zh-CN-XiaoxiaoNeural` MP3 files at `-4%`, with loudness normalization during final encoding. No TTS provider was re-invoked for V4.

Performance is the October 9 fixed-scheduler RXE comparison; version evidence is the October 12 Generation witness; the real terminal capture is October 16. Physical dual-HCA aggregation and end-to-end inference gains remain unmeasured. Python external-buffer failure semantics remain separate from Rust atomic publication.

## Rebuild and reuse

Document/media generation uses Python 3.10+, ReportLab, Pillow and FFmpeg; it does not change runtime dependencies. From the repository root:

```sh
MATERIALS=kv-service/verification/results/competition-demo-20261016-v4
EVIDENCE=kv-service/verification/results/competition-demo-20261016-v2
python3 "$MATERIALS/build_report_v4.py" --root "$MATERIALS" --evidence-root "$EVIDENCE"
python3 "$MATERIALS/build_guide_v4.py" --root "$MATERIALS"
python3 "$MATERIALS/render_video_v4.py" --root "$MATERIALS" --evidence-root "$EVIDENCE"
```

The copied skill folder can be placed under `~/.codex/skills/competition-submission-studio` on another machine; preserve an existing installation unless replacement is intended. Invoke `$competition-submission-studio` for production work. Its strategic counterpart remains `competition-research-design` and is unchanged. Publication, sending and official acceptance remain separate states governed by the current task's authorization.
