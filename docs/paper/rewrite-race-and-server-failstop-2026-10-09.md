# 唯一 Generation 并发读取与服务端清理故障：真实 RXE 补充验证

**日期：** 2026-10-09；**环境：** 同一宿主机上的两个隔离 KVM 客/服务端、两组虚拟链路和标准 Soft-RoCE Verbs。本页补充[五组性能实验](direct-staging-eval-2026-10-09.md)，不把 RXE 或测试 shim 的结果等同于实体 HCA。

## 主实验：每代唯一字节、SDK PUT 调用区间、SDK 接受身份

硬件门控的 Rust SDK 测试 [`unique_generations_survive_concurrent_direct_rail_reads`](../../kv-service/client-rs/tests/rail_read_e2e.rs)先写入 8 MiB、两条带、全 `0x10` 的 Generation 1；另一客户端连续提交十五次**内容互不相同**的覆盖，字节依次为 `0x20`–`0x2E`，对应 Generation 2–16。读端同时执行直接双轨读取：每次成功都逐字节确认整个返回值属于同一代，并把字节所指的 Generation 与 SDK 在二次 Lookup、最终发布后输出的 `RAIL_ACCEPTED_ID` Generation 逐次比较；任何拒绝都要求调用方目标保留入口哨兵 `0xA5`。写端先构造 8 MiB payload 并切分两段，**然后紧邻 `put_stream_chunks` 调用记录开始时刻**；结束时刻紧随该 SDK 调用返回，另记事后 Lookup 结束。读端用同一单调时钟记录起止。`UNIQUE_CONFIG` 在原始日志中声明 `put_interval_scope=sdk_call`。因此交叠计数确实属于**客户端 SDK PUT 方法调用**，但仍不能证明与服务端提交点或实际网络发送阶段相交。

| 轮次 | 唯一内容覆盖 | 读取总尝试 | 与 SDK PUT 调用相交 | 交错且完整接受 | 交错且安全拒绝 | 无该区间相交 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 15 | 20 | 15 | 1 | 14 | 5 |
| 2 | 15 | 20 | 16 | 5 | 11 | 4 |
| **合计** | **30** | **40** | **31** | **6** | **25** | **9** |

两轮总计 15 次完整接受、25 次安全拒绝、0 次缺失；其中**31 次**读取区间与 SDK PUT 调用相交，六次成功读的唯一字节均与 SDK 实际接受的 Generation 一致，25 次拒绝均保持哨兵。其余九次未与该方法调用相交。实际模式每轮恰为“读取总尝试＋最终读”次 direct、0 次其他模式；两条 Rail 都传输非零字节，末态在途请求、在途字节与注册预约均为 0。[主审计与每轮原始哈希](../../kv-service/verification/results/unique-generation-sdk-call-20261009/result.json)、[SDK 调用/读时间线论文图](figures/unique-generation-sdk-call-race-2026-10-09.svg)可交叉复核。另一台物理 SKV 节点使用 Python 3.8 [重算出全字段相同的 JSON](../../kv-service/verification/results/independent-skv2/sdk-call-independent.json)。

每轮**执行前**的清单记录测试二进制路径、SHA-256 和源码提交标签；服务端运行进程的 `/proc/PID/exe`、PID 及配置哈希在两轮前后不变。提交标签单独只是操作者声称，为此另在 Linux 构建主机保存了[编译前后源码与生成二进制收据](../../kv-service/verification/results/unique-generation-sdk-call-20261009/build-receipt/)：审计将其中 **20 份源码文件**的哈希逐一与 Git 提交 `9209d502` 比较，确认构建期间源码未变，且输出二进制哈希等于两次启动清单中的哈希。[构建审计结果](../../kv-service/verification/results/unique-generation-sdk-call-20261009/build-audit.json)是源码/构建归属证据，不是跨机器字节相同的 hermetic reproducible-build 证明。

这些只是**两个进程轮次、31 次观测到的 SDK PUT 调用/读区间交叠**，不能推出所有时序均安全。每代唯一内容与实际 SDK Generation 对应避免了旧 A/B 测试无法识别同内容、不同 Generation 条带混合的缺陷；无 checksum 对象的任意静默损坏、真正的提供者异常和代码层形式化证明仍未由它覆盖。

随后的一轮[Generation 切换见证实验](generation-transition-witness-2026-10-12.md)记录了八次同一读取的初查与复查分别返回旧/新 Generation，并且都安全拒绝。这提供“读取期间确有元数据版本切换”的直接见证；其单轮样本不与本节两轮计数相加，且未观测提交瞬间或 RDMA WRITE 是否仍在途。

## 历史连续性证据：明确降级

- 较早的[两轮每代唯一内容运行](../../kv-service/verification/results/unique-generation-put-20261009/result.json)提交 30 次覆盖、41 次读取；有 **36 次**与“**数据准备＋SDK 调用**”区间相交，十次完整接受、26 次拒绝。其起始时间戳早于 8 MiB payload 构造与分段，不能证明与 SDK PUT 调用相交；[旧时间线图](figures/unique-generation-put-race-2026-10-09.svg)和构建收据保留，绝不与新的 31 次相加。
- 先前的[三轮唯一 Generation 8 MiB 运行](../../kv-service/verification/results/unique-generation-20261009/result.json)也逐字节验证并比较了 SDK 接受 Generation，共 45 次覆盖、63 次读取、25 次完整接受、38 次安全拒绝。但当时写端只记了 **PUT 开始到事后 Lookup 结束**，其中 51 次与这一整轮相交**不是** 51 次经证明的 PUT 相交。测试二进制哈希确实在启动前保存，源码提交则只是操作者标签；缺少与提交逐文件比较的构建收据。它可支持有限的版本字节/接受身份一致性，不能替代上述较窄的客户端 PUT 尝试时间口径。
- 更早的 64/32 MiB 五轮 **A/B 内容复用**运行合计 75 次覆盖、100 次**读取总尝试**：55 次返回全 A/B 字节，45 次拒绝，未见 A/B 图案混合或失败哨兵变化。重复 A/B 内容不能发现 Generation 1/3 等同内容代际混合，也没有 PUT/读时间区间；旧 64 MiB 原始日志甚至缺少内嵌尺寸、有效 direct 模式和每 Rail 字节。保留[64 MiB](../../kv-service/verification/results/rewrite-race-64-20261009.json)与[32 MiB](../../kv-service/verification/results/rewrite-race-32-20261009.json)审计作为较弱历史材料，不将其 100 次说成竞争读取。

## 服务端：启动身份绑定的 QP/MR 错误返回注入

测试专用 [`libibverbs` shim](../../kv-service/verification/verbs_failstop_shim.c)在隔离服务端分别强制 `ibv_destroy_qp` 与 `ibv_dereg_mr` 返回 `-1`。[启动器](../../kv-service/verification/launch_server_failstop.sh)在执行前写下服务端二进制、shim、配置和脚本的**路径及 SHA-256**；客户端触发器也在 GET 前记录自己的二进制和对象键。QP 案例使用正常 128 MiB slab，MR 案例将 slab 缩为 4 MiB，使同一个 8 MiB 对象进入注册缓冲区后备源。两次客户端请求均失败而无完成摘要；服务端在到达相应清理路径后均以 shell 退出码 **134（SIGABRT）** 终止，事件文件分别只记 `destroy_qp` 或 `dereg_mr`。无注入服务端重新启动后，同一对象通过单轨完整读出，哈希 `fb2d91a1ef239d15`。[严格启动身份审计](../../kv-service/verification/results/server-failstop-runtime-20261009/result.json)验证清单、事件、退出码、失败客户端日志与恢复读；[另一物理节点重算](../../kv-service/verification/results/independent-skv2/server-runtime-independent.json)与其全字段一致。

这一注入只验证 **Verbs 调用报告错误时实现选择终止进程，不继续释放可能仍被 DMA 引用的内存**。shim 在软件提供者外层伪造返回值，未制造实体网卡或固件故障。fail-stop 通过终止服务进程换取保守清理边界，带来明确可用性代价，不能称为透明路径恢复。安全范围仍限 Rust SDK 多轨路径及其 KVService 服务端，不含 Python/`rdma-ffi` 外部目标缓冲区 API。

如需顶会级别强安全结论，还要在真实双 HCA 上重复并发覆盖与设备故障，采集 QP/MR/CQE 时间线，增加线程数与调度种子，并把[代码到状态模型的对应表](code-to-model-refinement-map-2026-10-09.md)升级为覆盖所有错误分支的严格模拟关系。
