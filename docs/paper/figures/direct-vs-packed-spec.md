# Figure specification: direct versus packed private staging

- **Question:** What memory movement changes when two Rails use direct ordered staging instead of packed receive buffers?
- **Conclusion:** Direct mode assigns the same consecutive stripe runs to the same two Rails, but the registered destinations are disjoint slices of one private object vector, removing the intermediate full-object assembly pass. Both modes still validate and copy once to the caller.
- **Entities:** one fixed 16-stripe object; Rail 0 owns stripes 0–7, Rail 1 owns 8–15; each Rail has its own QP/CQ and target MR. The packed control has two receive allocations and another ordered vector. Direct mode has one ordered vector split into two Rust mutable borrows plus one dummy MR per Rail (dummy omitted from the visual to preserve focus).
- **Edges:** arrows indicate RDMA WRITE into private target, then local assembly when present, then verification and caller publication. They do not indicate ownership transfer between Rails.
- **Lifetime:** neither panel exposes partial data to the caller. Direct slices remain owned by the request until publication or failure; QP retirement and MR deregistration precede any allocation reuse.
- **Evidence:** `kv-service/client-rs/src/rail_read.rs` direct/packed paths and matching-plan unit test. The 8/8 split is the equal-weight 16-stripe test fixture and 64 MiB RXE object, not a universal scheduler outcome. This is a mechanism diagram, not a performance result.
- **Format:** editable bilingual SVG and vector PDF, 500 pt width for a two-column systems paper.
