# Figure specification: private targets and safe publication

- **Question:** Why can one Rail fail or deliver a late WRITE without corrupting the caller's buffer?
- **Conclusion:** Every RDMA WRITE targets request-private registered memory. The caller sees bytes only after all Rails quiesce and coverage, checksums and descriptor identity pass. On uncertain completion, QP/CQ retirement precedes MR deregistration and buffer reuse.
- **Illustrative input:** one 16-stripe object, version `V0`; two Rails receive eight stripes each. The figure does not prescribe contiguous versus interleaved assignment and contains no measured performance numbers.
- **Source of semantics:** `KvClient::read_multi_rail_into`, `RailReader::read_staged_with`, `VerbsTransport::fetch`, server retained-source handling and [safety-contract.md](../safety-contract.md). The lower-copy direct mode is not shown as implemented.
- **Output:** editable English/Chinese SVG, vector PDF and PNG at 500 pt double-column width. The manuscript caption should state that version equality is checked at the second lookup, not guaranteed at arbitrary later time.
