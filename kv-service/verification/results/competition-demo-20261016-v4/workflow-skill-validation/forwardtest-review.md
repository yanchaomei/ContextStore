# competition-submission-studio 独立 forward test

结论：现有 RailFuse 材料的本地打包与旁白时长检查已实际完成，可复用于同类本地附件准备。当前没有未关闭的 P1/P2。首次发现 2 个 P2 后，由主任务授权的另一实现代理做最小修复；本测试者保留原失败输入、输出与修复前源码快照，以同一输入、新输出路径独立重测，46/46 条最终检查均符合预期。

本次严格限定于现有本地材料，不联网，不启动产品运行时或远程实验，不发消息/邮件，不运行 git；所有工作产物位于本目录。技能源文件未编辑。另有一次 Python 默认字节码缓存副作用，见执行边界记录。

## 用户输入与测试方法

真实请求：“请把现有 RailFuse 材料整理为可交付附件，检查每段旁白是否能在场景内播完，并保留文件校验。只准备本地包，本次没有授权发邮件或推送仓库。”

输入边界见 task-input.json；复制来源及冻结 SHA-256 见 copy-manifest.json。只枚举了指定源目录的顶层与 narration 目录，没有遍历其它大目录；只复制两份 PDF、一个 MP4、分镜 JSON 和 8 份 MP3。原分镜没有 audio 字段，保留原件，并在 video-scenes-for-audit.json 中只新增 audio=narration/<id>.mp3；不改文本、时长、语速或音频。

先按 CLI 帮助与样例完成真实主线，再独立设计并执行第一批 40 项边界/对照；未预先阅读脚本实现。随后读取脚本定位风险，补充 5 项检查。所有命令、实际退出码、stdout/stderr 见 commands.jsonl；输入与自动化脚本均保留。tested-skill-snapshot 和 tested-skill-files.json 记录本次使用的技能文本与源文件哈希。

## 实际完成的本地交付

- RailFuse-local-attachments.zip：15 个 payload 文件与 2 个内部清单，总计 10012638 字节。
- ZIP SHA-256：`173af6038e93e293be0a3957d2845862b0b24cb055665f37725b3281c5f741da`。
- 真实旁白 CLI、ZIP build、独立 verify 均退出 0。另用独立 zipfile+hashlib 逐文件读出并比较全部 15 个 payload 字节与冻结 SHA-256；全部匹配。
- 视频 ffprobe：H.264/AAC、1920×1080、270.000 秒；完整解码 ffmpeg 退出 0、无 stderr。
- 单独再次测量 8 份音频并独立计算时长预算与音频哈希；与 helper 输出一致。
- 全部选定只读来源在测试末尾重新计算哈希，仍与复制时的冻结值一致。

| 场景 | 场景秒数 | 音频秒数 | 扣除 0.3 秒尾部余量后的剩余秒数 |
| --- | ---: | ---: | ---: |
| intro | 25.000 | 21.240 | 3.460 |
| architecture | 30.000 | 25.512 | 4.188 |
| read | 40.000 | 25.680 | 14.020 |
| performance | 35.000 | 25.752 | 8.948 |
| version | 35.000 | 24.432 | 10.268 |
| late | 40.000 | 27.648 | 12.052 |
| put | 35.000 | 24.072 | 10.628 |
| reproduce | 30.000 | 22.488 | 7.212 |

8 段场景合计 270.000 秒，音频合计 196.824 秒；无超时。最紧段 intro 仍有 3.460 秒余量。

## 首次发现并经独立重测关闭的 P2

### P2-1（已关闭）：有限场景时长相加溢出后仍返回成功，并写出无效 JSON

位置：scripts/check_narration.py:130-144；scripts/_audit_common.py:45-46。
输入：两个独立场景都使用现有 1 秒 WAV，每段 duration_seconds=1e308，省略顶层总长。每段本身是有限正数，sum 得到 inf，没有再检查总长有限性；json.dumps 默认输出 Infinity。
最小复现：

```sh
python3 -B /Users/yanchaomei/.codex/skills/competition-submission-studio/scripts/check_narration.py --root /tmp/competition-submission-studio-forwardtest/boundaries --scenes /tmp/competition-submission-studio-forwardtest/boundaries/narration_total_overflow.json --output /tmp/competition-submission-studio-forwardtest/boundaries/overflow-repro-new.json
```

已执行同等命令的实际退出码：0；stdout 为 pass；产生 narration_total_overflow-audit.json，其中 total_scene_duration_seconds 为 Infinity。独立 strict JSON 验证器退出 2，stderr 为“non-standard JSON token: Infinity”。
预期：输入错误退出 2，不产生成功审计。建议对累加后的 total 再做 isfinite，并用 allow_nan=False 写 JSON；这是极端输入下的边界错误，本次正常分镜不受影响。

### P2-2（已关闭）：异常 ffprobe 流对象触发未捕获异常，退出 1 与超时语义混淆

位置：scripts/check_narration.py:59-64 和 :170-176。
输入：--ffprobe 指向本地测试进程 boundaries/malformed-ffprobe；该进程退出 0 并返回有效 JSON `{"streams":[null],"format":{"duration":"1"}}`。列表中的成员未确认是对象即调用 stream.get，产生 AttributeError。

```sh
python3 -B /Users/yanchaomei/.codex/skills/competition-submission-studio/scripts/check_narration.py --root /tmp/competition-submission-studio-forwardtest/boundaries --scenes /tmp/competition-submission-studio-forwardtest/boundaries/narration_malformed_probe_stream.json --output /tmp/competition-submission-studio-forwardtest/boundaries/malformed-probe-repro-new.json --ffprobe /tmp/competition-submission-studio-forwardtest/boundaries/malformed-ffprobe
```

实际退出码：1；stderr 为 AttributeError traceback；没有审计文件。约定的 1 表示“至少一段旁白超时”，因此上层自动化会误分原因。预期是错误退出 2，结构化 error，无成功审计。建议在访问前检查每个 stream 是 dict。这里通过可配置探针的本地替身注入错误，不声称标准 ffprobe 的正常输出会包含 null。

## 全部边界与对照结果

45 个检查条目：43 个符合预期、2 个不符合预期。条目包含纯 CLI 负例、正常/精确边界对照、同输入确定性字节比较及一个无音轨视频夹具准备动作；不把它们全部称为独立负例。输入错误负例预期退出 2 且无新成功审计，超时负例预期退出 1 且保留超时审计。已有输出检查还比较原字节未变。

| 检查 | 预期 | 实际 | 结论 |
| --- | --- | --- | --- |
| narration_exact_margin | 0 | 0 | 符合预期 |
| narration_overrun_one_millisecond | 1 | 1 | 符合预期 |
| narration_inconsistent_total | 2 | 2 | 符合预期 |
| narration_duplicate_ids | 2 | 2 | 符合预期 |
| narration_boolean_duration | 2 | 2 | 符合预期 |
| narration_nan_duration | 2 | 2 | 符合预期 |
| narration_negative_lead_in | 2 | 2 | 符合预期 |
| narration_missing_audio | 2 | 2 | 符合预期 |
| narration_symlink_audio | 2 | 2 | 符合预期 |
| narration_symlink_parent | 2 | 2 | 符合预期 |
| narration_path_escape | 2 | 2 | 符合预期 |
| narration_non_media | 2 | 2 | 符合预期 |
| narration_missing_ffprobe | 2 | 2 | 符合预期 |
| fixture_video_no_audio | 0 | 0 | 符合预期 |
| narration_no_audio_stream | 2 | 2 | 符合预期 |
| narration_existing_output | 2 | 2 | 符合预期 |
| package_valid | 0 | 0 | 符合预期 |
| package_missing_source | 2 | 2 | 符合预期 |
| package_wrong_hash | 2 | 2 | 符合预期 |
| package_source_escape | 2 | 2 | 符合预期 |
| package_archive_escape | 2 | 2 | 符合预期 |
| package_source_symlink | 2 | 2 | 符合预期 |
| package_source_symlink_parent | 2 | 2 | 符合预期 |
| package_case_collision | 2 | 2 | 符合预期 |
| package_unicode_collision | 2 | 2 | 符合预期 |
| package_prefix_collision | 2 | 2 | 符合预期 |
| package_windows_reserved_name | 2 | 2 | 符合预期 |
| package_internal_namespace | 2 | 2 | 符合预期 |
| package_backslash_path | 2 | 2 | 符合预期 |
| package_low_byte_budget | 2 | 2 | 符合预期 |
| package_existing_zip | 2 | 2 | 符合预期 |
| package_zip_equals_audit | 2 | 2 | 符合预期 |
| package_repeat | 0 | 0 | 符合预期 |
| package_deterministic_bytes | True | True | 符合预期 |
| verify_tampered_payload | 2 | 2 | 符合预期 |
| verify_extra_member | 2 | 2 | 符合预期 |
| verify_traversal_member | 2 | 2 | 符合预期 |
| verify_symlink_member | 2 | 2 | 符合预期 |
| verify_invalid_zip | 2 | 2 | 符合预期 |
| verify_low_byte_budget | 2 | 2 | 符合预期 |
| narration_conflicting_duration_fields | 2 | 2 | 符合预期 |
| narration_zero_budget | 2 | 2 | 符合预期 |
| narration_duplicate_json_key | 2 | 2 | 符合预期 |
| narration_total_overflow | 2 | 0 | 不符合预期 |
| narration_malformed_probe_stream | 2 | 1 | 不符合预期 |

## 授权、证据与主观质量边界

技能正文与交付参考明确区分“本地生成、字节回读、内容审阅、视觉/试听、commit、push、发信、门户提交、接受”。实际 helper 输出也将 spoken_text_completeness、subjective_audio_review、audio_visual_sync、publication_status 等标为 not_assessed，ZIP 审计明确 content_review/visual_review/submission_status 未核实，secret_scan 未执行。它们未把机械通过升级成主观质量或发布成功。包内“本地附件范围与未核实项.txt”也明确该边界。

没有验证：PDF 逐页渲染与版式、音频试听/发音/自然度、是否把文本完整读完、最终音画同步、原始运行/源码/实验主张、source_commit 真伪、赛事规格/截止时间、附件内全部敏感信息。MP4 完整解码只说明解码流程通过，不等于视觉、听觉或证据真实性审阅。原材料含有的既往仓库/推送等陈述不作为本次外发回执。

故本次成果可称为“现有材料本地附件包已生成，8 段旁白时长预算通过，包内字节完整性已核”，不能称为“材料技术内容全面通过、最终听审通过、赛事正式提交或已接受”。

## 执行边界与可复用性

第一次按技能示例直接运行 python3 --help 时，Python 导入 _audit_common 创建 scripts/__pycache__/_audit_common.cpython-310.pyc。birthtime=2026-10-16T22:55:14.508789，与首轮日志创建时间22:55:14.467503吻合。这是测试操作者没有预先关闭字节码生成的疏漏，违反了“仅向 /tmp 写入”约束；已向主任务如实报告，后续统一 python3 -B。没有编辑技能源文件，没有把缓存纳入技能快照或交付包，也未擅自清除源目录；主任务已表示可在所有测试结束后清理这一个缓存。缓存本身不作功能阻塞。
主线脚本仅标准库，已有 ffprobe/ffmpeg 可用时能直接复用；对只读环境推荐调用 python3 -B。原材料未写 audio 字段需要显式映射，这不是工具错误，技能说明已经要求该字段。工具能保留授权与证据状态边界。真实正常输入和主要路径/哈希/输出覆盖防护均通过；上述 2 个 P2 已经修复且由本测试者使用保留输入独立复验通过；首次失败记录仍保留，不用主线通过替代边界修复。

所有输出应保持本地；本次没有外发授权。完整日志 commands.jsonl、测试输入 boundaries/、三份运行脚本、独立校验 JSON 和源文件哈希均可供复查。

## 修复后独立重验回执

修复后的 check_narration.py SHA-256：`91f8e8ab3b91290e383a3fee6501cc9ecef74a9a5964e42db75f8cd409486561`；执行前后均核对不变。源文件副本保存在 tested-skill-postfix-snapshot/scripts/check_narration.py。原失败版本保留在 tested-skill-snapshot；原审阅结论保留在 review-before-fix.md。实现代理只新增聚合总长有限性检查与逐个 stream 对象类型检查，本测试者未修改技能。

run_postfix.py 从原记录重新运行 43 个 CLI 输入案例（包括 2 个原失败），使用同一输入与新输出名；复核确定性 ZIP 字节后，再运行真实 8 段旁白及原本地包回读，共 46 项检查、46 项符合预期。无音轨 MP4 支持夹具复用首次产物，没有把重复生成夹具计入修复后结果。详细命令、实际退出码、stdout/stderr、是否产生审计见 postfix-independent-results.json 与 commands.jsonl 的 postfix_independent_retest 记录。

| 原问题 | 修复后实际结果 | 审计文件 | 结论 |
| --- | --- | --- | --- |
| 时长总和溢出 | 退出 2；结构化错误 total scene duration must be finite and positive | 未产生 | P2-1 关闭 |
| probe streams 含 null | 退出 2；结构化错误 ffprobe returned an invalid stream | 未产生 | P2-2 关闭 |

真实材料复验：8 段时长 CLI 退出 0；原包 verify 退出 0、15 个 payload、ZIP 哈希仍为 `173af6038e93e293be0a3957d2845862b0b24cb055665f37725b3281c5f741da`。首次与修复后真实旁白审计的字节完全相同：True。此次修复没有改动交付包或音频。

最终状态：本地附件已准备、每段音频时长预算通过、ZIP 字节完整性已核、工具已发现的 P2 已关闭。尚未执行的视觉/主观听感/文本完整性/音画同步与运行证据核实仍未核实；任何邮件、仓库推送或正式提交均未执行。
