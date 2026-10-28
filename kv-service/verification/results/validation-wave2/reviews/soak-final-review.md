# Wave 2 v3 两小时 soak 独立终审

**PASS，仅限本次健康单 HCA 场景的逐次读取正确性与采样资源稳定性。** 冻结 v3 程序真实连续运行 **7,200.289350 秒 steady**，完成 **71,960 次 steady 读取，603,644,231,680 逻辑字节（562.1875 GiB）**。加上 80 次预热、4 次重新打开验证，共 **72,044 次读取，604,348,874,752 逻辑字节**。原生测试与 runner 均正常退出 0；没有 watchdog、输出预算终止或用空等填满两小时。

本审查只读取本地闭合证据，并运行离线 Python 重放；未构建 Rust、未重跑 native workload、未更改 harness、runner 或生产代码。无剩余实质性验收问题，下面的证据边界必须与 PASS 一同保留。

## 冻结身份和证据完整性

- 生产基础 revision：`1c3ee135d19fece67507b51acd1fc51ae9541208`。独立顺序读取 base tar 与最终 source tar，比较 **2,485 个基础文件**，全部内容一致；只有新 soak 测试与 runner 两个文件新增。
- 原生 PID：`2780409`，start ticks：`1334490144`；启动、结束和运行中身份回执一致。采集回执记录原 native/runner 进程已不存在。
- 二进制 SHA256：`5aa06344cb6d56c55de119d3ffd01a8c654423bf98084e25590f73ff97ebe1df`；Rust harness：`6f7d8113f58a34a28faedda8692421e2f847b2f52d1e965f6bd836e083a9c512`；runner：`60f815a95a7772d0bf8875452aa0caa3a10196a934befa630761435526777714`。
- 独立核对全部 41 个 manifest 文件及 outputs 镜像的大小和 SHA256；6 个冻结文件的运行后远端校验全部 OK。保留正向 oracle 退出 0、负向 oracle 退出 101。
- `stdout.log.gz`、`stderr.log.gz` 均只有一个完整 gzip member，独立校验闭合 trailer、CRC32 和 ISIZE。解压 stdout **22,396,763 字节**，stderr **0 字节**；与 runner 原始字节数精确一致，低于 500,000,000 字节预算。
- 解析全部 **73,502 个 JSON 事件**，事件类型和 outcome 计数与最终回执一致；恰有一条成功 summary，native libtest footer 为 1 passed / 0 failed。

## 逐次内容、对象身份与时间

| 阶段 | 成功读取 | 逻辑字节 |
|---|---:|---:|
| 预热 | 80 | 671,088,640 |
| 同一 Reader 的 steady | 71,960 | 603,644,231,680 |
| 新 Reader 重新打开 | 4 | 33,554,432 |
| 合计 | 72,044 | 604,348,874,752 |

所有 terminal 序号恰为 `0..72043`，无重复、无缺号；每组 4 次请求完成后才进入下一组。每条记录的对象选择符合 `seq % 3`，3 个键分别读取 24,015 / 24,015 / 24,014 次。每条返回长度均为 8,388,608 字节，`exact_bytes` 与 `same_identity` 均为 true，actual/expected hash 一致，identity error 为空，结果均为 `Ok(Some(8388608))`。错误、deadline、rejection 均为 0。

离线审计用独立 XXH64 reference arithmetic（含空串与 hello 已知向量自检）重新构造完整三种 8 MiB 模式：`(offset % 251) XOR (seed * 53)`，seed=0/1/2。得到哈希 `c8ea541e2a830728`、`130f73893d9baf35`、`e7b0eaa5e1c40302`，与初始对象和每条读取记录逐一吻合。这里使用 **XXH64 seed 0**，不是 XXH3。

冻结源码在每次读取后实际比较整个目的缓冲区，并重新 lookup，比较整个 `descriptor` 与 `placement`；初始完整对象描述和 placement 已保留。原始文件保存的是校验结果与哈希，没有转储每次 8 MiB payload 或每次 lookup 的完整返回值，因此离线复核重算的是已知期望模式，而非重新读取远端对象。

steady 时间由同一个 Rust `Instant` 驱动，开始于 80 次预热结束；同一个 Reader 一直保持到 steady 完成。循环中没有 sleep 填时、Reader 重建或提前退出后补时。活动采样的 1ms sleep 与未完成的读取 futures 并行，仅用于观察。整个进程的 runner 单调时钟耗时 **7,209.636557 秒**，包含初始化、预热和清理。

主 Reader 的 **240 个资源点**构成 239 个递增间隔，每个间隔都有实际读取完成：36–304 次；间隔 **3.593309–30.426883 秒**，短尾段为正常收尾。原始请求没有逐次绝对时间戳，连续工作结论来自冻结循环、完整分组请求、各区间递增计数和实际活动观测，不能用于推断未采样时刻的精确瞬时速率。

## 资源、计数和关闭

241 条持久化 reconciliation 与 241 条资源记录均按当时已记录的 terminal 请求数独立核对。主 Reader 结束时 `created=4, reused=72036, retired=0, live=4`，满足 `created−retired=live`、`created+reused=72040`，`reads_ok=72040` 和 `rail bytes=72040×8 MiB`。新 Reader 独立满足 4 次读取的同样等式。源码还在每一批 4 次读取后做这些检查；只有周期点写入原始资源日志。

所有资源快照都处于一批读取结束后的 quiescent 阶段：active/connecting/retiring、inflight requests/bytes、reserved registration bytes 均为 0，idle=live=4。这些零值是静止点观测，不能写成活动期值或已注册 MR 的实测数量。

活动期使用独立的有界标量累计计数：预热 **1,286 / 2,035** 次采样观察到 active；steady **1,168,687 / 1,854,739** 次采样观察到 active，max active=4、max live=4。每个相邻 steady 累计点都新增活动观测。生产端逐次 latency 随日志输出，不保存持续增长的历史 latency 数组。

| 资源 | 运行前 | 预热后 | 主 Reader steady 结束 | 第一次 Drop 后 | 重新打开后 |
|---|---:|---:|---:|---:|---:|
| FD | 11 | 23 | 23 | **11** | 23 |
| RSS，KiB | 58,448 | 78,084 | 86,316 | 86,312 | 86,488 |
| Threads | 6 | 10 | 10 | 10 | 10 |

主 Reader 的资源曲线里 FD 恒为 23、threads 恒为 10。RSS 采样最小 **78,048 KiB**、最大 **87,040 KiB**，steady 终值较预热增加 **8,232 KiB（8.0390625 MiB）**，采样峰值较预热增加 **8,956 KiB（8.74609375 MiB）**。终点斜率为 **1.143287387 KiB/s**，仅为两个端点差值除以时长；不能当作泄漏速率。全部采样和第一次 Drop 通过预先冻结的 FD warm+8、threads warm+4、RSS warm+128 MiB 诊断边界。RSS 没有回到起点，契约也没有要求其精确回零。

第一次 Drop 的 FD 精确恢复 11，RSS/threads 的 before/warm/after 与 signed deltas 一致。随后新 Reader 完成 4 次全量正确读取，源码显式 Drop 后进程正常退出。第二次 Drop 的 FD/native 数量没有另行采样，不宣称再次实测恢复。

## PPT 可用素材与声明范围

- `soak-ppt-summary.json`：可直接渲染的摘要、数字、分阶段资源、中文脚注。
- `soak-resource-curve.csv`：从 raw 重算的 **240 个主 Reader 实际资源点**。横轴是 steady `Instant` 秒/分钟；warm 点为源码赋值的零基线。未混入 reopened 的另一个 `elapsed_s=0`，未为 Drop 伪造时间戳。
- `soak-final-review.json`：独立计算结果、原始 checksum、计数、资源极值、证据边界。
- `soak-final-review.py`：可重复的离线审计，读取闭合 raw、冻结源码和 archives，生成上述 JSON/CSV。执行方式为 `python3 outputs/validation-wave2-20261029/reviews/soak-final-review.py`。

**必须保留：**本次是 SKV2 单个 `mlx5_1` HCA 到 SKV4 debug provider，Rust test profile（未优化、带 debug info）的正确性与采样资源验证。逻辑 API 字节不是实测 wire 字节；不得作为发布版性能、双 HCA 扩展、故障恢复或 FFI 证据。原生 PID-owned **QP/CQ/MR 一直为 unavailable**；没有正向归属观测，不能把 CLI 的 0 匹配项或 reserved byte=0 写成零 MR 泄漏证明。采样资源守界也不是数学意义的零泄漏证明。

v2 的 intentional termination 和 `LIMITED_SUPERSEDED` 回执保持原样；其时间、请求数和任何初步状态均未计入本次 v3 两小时通过。
