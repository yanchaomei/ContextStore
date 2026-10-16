# Voice selection and synthesis record / 2026-10-16

The user requested a clear, friendly female voice. V3 uses the Mandarin neural female voice `zh-CN-XiaoxiaoNeural` through Microsoft Edge online speech, with `edge-tts` 7.3.1 and rate `-4%`. The previous V2 video used macOS Tingting. This update synthesizes all eight spoken segments again; it does not relabel or process the old Tingting recording.

## What was researched

- [MiniMax synchronous speech](https://platform.minimax.cn/docs/api-reference/speech-t2a-http): the official API documents `speech-2.8-hd`, configurable voice/speed/emotion and pronunciation controls. [Official pricing](https://platform.minimax.cn/docs/pricing/overview) was checked. This provider requires its own credentials; no MiniMax sample was generated for this submission.
- [Doubao Voice official model list](https://docs.volcengine.com/docs/DoubaoVoice/model-list?lang=zh): the official documentation lists Seed-TTS 2.0 voices. [Long-form task submission](https://docs.volcengine.com/docs/DoubaoVoice/Tasksubmission?lang=zh) requires provider credentials. No Doubao audio was generated here.
- [Fish Audio developer page](https://fish.audio/zh-CN/developers/) and [text-to-speech documentation](https://docs.fish.audio/features/text-to-speech): a further candidate for expressive narration. Its public web trial could not be verified through the browser in this session; no Fish sample was produced.
- [Microsoft official speech language/voice support](https://learn.microsoft.com/en-us/azure/cognitive-services/speech-service/language-support?tabs=tts): checked the Chinese neural voice family. The audio actually used here came through the Edge online endpoint, not a paid Azure API account.
- [edge-tts maintainer repository](https://github.com/rany2/edge-tts) and [7.3.0 release](https://github.com/rany2/edge-tts/releases/tag/7.3.0): an old 7.2.7 client could list voices but received HTTP 403 for synthesis. Updating only an isolated temporary client to [7.3.1](https://github.com/rany2/edge-tts/releases/tag/7.3.1) restored successful synthesis. No project dependency or runtime source was changed.

## What was actually compared and delivered

The same short technical paragraph was synthesized with Xiaoxiao, Yunxi and Yunyang. All three samples were saved; the user then selected a clear, friendly female style. Xiaoxiao is the female sample selected for the finished video. This is a preference-based choice among the generated samples, not a measured naturalness ranking against providers whose audio was not generated.

Every final segment has a saved MP3, sentence boundary data, text hash, audio hash and measured duration. All eight fit their scene without truncation or accelerated playback. Acronyms were rewritten into spoken Chinese where possible; the visual evidence retains exact technical field names. Only loudness normalization was applied when encoding the soundtrack. Final H.264/AAC decoding and loudness measurements are recorded separately. Captured speech is included so replay does not need an online synthesis service.

The SRT sidecar follows the provider's sentence boundaries. It is optional and is not burned over the terminal evidence. Online endpoint availability or future voice output is not guaranteed; the included MP3s and hashes identify this delivered recording.
