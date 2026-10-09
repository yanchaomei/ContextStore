# 从抽象事件到 Rust/Verbs 路径的对应表

**目的：** 让 [19,657 状态的两 Rail 探索器](../../kv-service/verification/multirail_state_explorer.py)与实际实现可以逐项审查。下表是**对应论证草图**，不是已经证明的 refinement relation；模型未执行 Rust 代码，也未模拟真实提供者的 CQE、内存注册或进程崩溃。

| 抽象事件或不变量 | 实现对应点 | 可核验证据 | 仍需假设或未覆盖情形 |
| --- | --- | --- | --- |
| `Lookup0` 固定候选版本 | [`KvClient::read_multi_rail_into`](../../kv-service/client-rs/src/lib.rs)首次 `LookupObject`，保留 Descriptor 与 Placement；[`RailPlan::build`](../../kv-service/client-rs/src/rail_read.rs)验证对象大小、条带索引、所有者和目的区间 | Descriptor/Placement 单元测试；五组性能日志中的前后身份指纹 | 模型仅有 A/B 两版本；元数据身份碰撞、无标识的原地修改由存储协议假设排除 |
| 原子准入与资源边界 | [`RailReader::reserve`](../../kv-service/client-rs/src/rail_read.rs)在开 QP 前预约总暂存、注册/在途字节和每 Rail 容量；`BudgetGuard` 随请求/暂存对象释放 | 上限单元测试；审计核对每次基准后每 Rail 预约归零 | **预算计数不在状态模型内**；跨请求并发与进程崩溃需另行验证 |
| `Post(r)`、`Complete(r)`、`covered[r]` | 客户端 [`VerbsTransport::fetch_into`](../../kv-service/client-rs/src/rail_read.rs)构建带目标/哑元 MR 的 SGE，调用 Verbs GET；服务端验证请求条带并按实际完成字节回复 | SGE 越界/条带覆盖测试；最终每 Rail 字节、完成数量和同对象哈希审计 | 模型把 CQE 与覆盖事件视为可信输入；没有解释每个 CQE 如何映射到状态变量 |
| `Timeout(r)`、`RetireQP(r)`、`Deregister(r)` | [`RdmaClient::retire_after_control_error`](../../kv-service/client-rs/src/rdma.rs)及 Drop 销毁 QP；直接暂存路径显式先 drop 客户端、后 drop 两个 MR；QP/MR 销毁返回错误时使用不可 panic 的底层写入后 `abort` | 最终 RXE 晚到 WRITE 哨兵测试；客户端测试 shim 在 stderr 关闭时分别注入两个错误返回 | 成功的 `ibv_destroy_qp` 阻止后续 WRITE 是提供者假设；测试 shim 不是实体网卡故障 |
| 服务端 `source[r]` 保持固定 | [`RcQp::retain_uncertain_*`](../../kv-service/server/src/rdma/qp.rs)保存 MR、Bytes、slab extent/pin；QP Drop 先成功销毁，持有源随后才可释放；[`MemRegion::drop`](../../kv-service/server/src/rdma/context.rs)检查注销结果 | CQ 超时后 `0/32` 与连接退役日志；服务端 QP/MR 错误返回注入及恢复读 | 服务端测试触发了软件返回错误；没有证明真实固件/设备重置行为 |
| `Join` 与共享暂存向量所有权 | [`RailReader::fetch_direct_staged`](../../kv-service/client-rs/src/rail_read.rs)用 `split_at_mut` 分割一个向量，`thread::scope` 加入全部 worker 后才读取/释放；`StagedRailRead` 在二次查询和发布期间持有向量与预算 | 目标切片不重叠/预算保留单元测试；唯一 Generation SDK 调用实验两轮的实际 direct 计数、两 Rail 字节及终态预约归零 | 模型保守地让两 slice 在成功发布或失败并全员 join 前不可复用；未建模 Rust 借用检查器本身 |
| 覆盖、校验、`Lookup1` 与版本匹配 | [`RailReader::read_staged_with`](../../kv-service/client-rs/src/rail_read.rs)验证每条带实际长度和已提供的 xxh3；SDK 二次 `LookupObject`；[`commit_if_unchanged`](../../kv-service/client-rs/src/rail_read.rs)比较 Descriptor 与 Placement | 校验/缺失条带单元测试；唯一 Generation 实验有 31 次 SDK PUT 方法调用与读区间相交（数据准备已结束），其中六次返回字节与 SDK 接受 Generation 一致、25 次安全拒绝；服务端提交相交次数未知；构建收据逐文件核对源码与二进制 | **无 checksum 时无法检测任意静默损坏**；成功返回不保证二次查询后对象永不变化 |
| `Cancel` 与 `Publish` 有一条顺序 | [`RailCancel::cancel` 和 `publish_if_live`](../../kv-service/client-rs/src/rail_read.rs)共用最终复制门锁；RDMA 从不直接写调用方缓冲区 | 单元测试、真实 RXE 取消/晚到 WRITE 测试；失败哨兵在重写压力中保持原值 | 假设调用方遵守 Rust `&mut [u8]` 独占借用，并在返回/取消完成后访问；不覆盖外部 unsafe 别名 |

独立审查发现并推动修复了三个模型/实现表述缺口：仅以 QP 退役允许发布前复用直接暂存区、失败时允许单个 slice 在其他 worker 未退出前复用，以及将“短 WRITE”负控与其他缺失守卫混在一起。最新模型将共享向量的失败释放放在两 QP 退役和全员 join 之后；短 WRITE 负控保留完成、退役、join 和版本条件，只放松覆盖检查。[原始模型与修订结果](safety-model-plan.md)均保留，以免把审查前的状态数当作现有结论。

**范围边界：** 本表只讨论 Rust SDK 多轨读与其 KVService 服务端。独立的 Python/`rdma-ffi` 外部目标缓冲区接口、进程崩溃、未观测到的提供者故障、资源预算的形式化闭环以及任意条带数的完全状态空间均不在模型已证明范围内。要把本表升级为论文级形式化证明，需要逐条建立代码状态到抽象状态的模拟关系，包含每个错误返回、Drop 顺序和 CQE 分类，并在真实提供者上验证退役语义。
