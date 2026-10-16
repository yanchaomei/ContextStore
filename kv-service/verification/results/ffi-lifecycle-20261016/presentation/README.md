# Competition presentation, 2026-10-16

The eight-page PDF and four-minute MP4 describe the code revision in `code-version.txt`. The video is an edited narrated view of actual 2026-10-16 outputs and selected original receipt fields, not a continuous desktop recording. Full source receipts remain in the parent evidence directory; `RailFuse-demo-capture-20261016.json` binds each scene to its raw files and the frozen source manifest.

The MP4 contains H.264 1280x720 video and AAC audio, exactly 240 seconds. The Chinese narration uses the macOS Tingting voice at rate 135; every complete utterance fits within its 30-second scene. `video-audio-qa.json` preserves the measured utterance durations. Rendering requires macOS `say`, the named fonts, Pillow and ffmpeg; PDF generation requires ReportLab. These are artifact-generation tools, not runtime dependencies of ContextStore.

The document separates Rust RailReader failure-atomic publication from the legacy single-connection Python FFI. Controlled CQ-poll suppression and Soft-RoCE/KVM scope remain disclosed. This functional demonstration is not a statistically controlled speedup claim. Artifact preparation and source publication do not constitute formal competition submission or organizer acknowledgment.
