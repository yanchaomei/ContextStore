/* Test-only: hide genuine RXE CQ entries until the originating QP is retired. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <infiniband/verbs.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static int (*provider_poll)(struct ibv_cq *, int, struct ibv_wc *);
static int retired;
static void record(const char *event) {
    const char *path = getenv("CS_CQ_DELAY_LOG");
    if (!path) _exit(90);
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (fd < 0) _exit(91);
    size_t length = 0;
    while (event[length]) ++length;
    if (write(fd, event, length) != (ssize_t)length) _exit(92);
    close(fd);
}
static int delayed_poll(struct ibv_cq *cq, int count, struct ibv_wc *wc) {
    if (!retired) return 0;
    int n = provider_poll(cq, count, wc);
    if (n > 0) {
        char event[160];
        snprintf(event, sizeof(event), "real_completion,qp=%u,wr=%llu,status=%u\n",
                 wc[0].qp_num, (unsigned long long)wc[0].wr_id, wc[0].status);
        record(event);
    }
    return n;
}
struct ibv_cq *ibv_create_cq(struct ibv_context *ctx, int entries, void *context,
                          struct ibv_comp_channel *channel, int vector) {
    struct ibv_cq *(*next)(struct ibv_context *, int, void *, struct ibv_comp_channel *, int) =
        dlvsym(RTLD_NEXT, "ibv_create_cq", "IBVERBS_1.1");
    if (!next) { errno = ENOSYS; return NULL; }
    struct ibv_cq *cq = next(ctx, entries, context, channel, vector);
    if (cq) {
        provider_poll = ctx->ops.poll_cq;
        ctx->ops.poll_cq = delayed_poll;
        record("cq_delay_installed\n");
    }
    return cq;
}
int ibv_destroy_qp(struct ibv_qp *qp) {
    int (*next)(struct ibv_qp *) = dlvsym(RTLD_NEXT, "ibv_destroy_qp", "IBVERBS_1.1");
    if (!next) { errno = ENOSYS; return -1; }
    int result = next(qp);
    if (!result) {
        retired = 1;
        record("qp_destroyed_reveal_real_cq\n");
    }
    return result;
}
