/* Test-only libibverbs interposer for isolated RXE guests. Never preload in production. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <infiniband/verbs.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void record(const char *event) {
    const char *path = getenv("CS_FAIL_VERBS_LOG");
    if (!path) return;
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (fd < 0) return;
    ssize_t written = write(fd, event, strlen(event));
    (void)written;
    (void)close(fd);
}

static int inject(const char *name) {
    const char *requested = getenv("CS_FAIL_VERBS_OP");
    return requested && strcmp(requested, name) == 0;
}

int ibv_destroy_qp(struct ibv_qp *qp) {
    record("destroy_qp\n");
    if (inject("destroy_qp")) {
        errno = EIO;
        return -1;
    }
    int (*next)(struct ibv_qp *) = dlvsym(RTLD_NEXT, "ibv_destroy_qp", "IBVERBS_1.1");
    if (!next) {
        record("destroy_qp_lookup_failed\n");
        errno = ENOSYS;
        return -1;
    }
    return next(qp);
}

int ibv_dereg_mr(struct ibv_mr *mr) {
    record("dereg_mr\n");
    if (inject("dereg_mr")) {
        errno = EIO;
        return -1;
    }
    int (*next)(struct ibv_mr *) = dlvsym(RTLD_NEXT, "ibv_dereg_mr", "IBVERBS_1.1");
    if (!next) {
        record("dereg_mr_lookup_failed\n");
        errno = ENOSYS;
        return -1;
    }
    return next(mr);
}

int ibv_destroy_cq(struct ibv_cq *cq) {
    record("destroy_cq\n");
    int (*next)(struct ibv_cq *) = dlvsym(RTLD_NEXT, "ibv_destroy_cq", "IBVERBS_1.1");
    if (!next) next = dlsym(RTLD_NEXT, "ibv_destroy_cq");
    if (!next) { errno = ENOSYS; return -1; }
    return next(cq);
}

int ibv_dealloc_pd(struct ibv_pd *pd) {
    record("dealloc_pd\n");
    int (*next)(struct ibv_pd *) = dlvsym(RTLD_NEXT, "ibv_dealloc_pd", "IBVERBS_1.1");
    if (!next) next = dlsym(RTLD_NEXT, "ibv_dealloc_pd");
    if (!next) { errno = ENOSYS; return -1; }
    return next(pd);
}

int ibv_close_device(struct ibv_context *ctx) {
    record("close_device\n");
    int (*next)(struct ibv_context *) = dlvsym(RTLD_NEXT, "ibv_close_device", "IBVERBS_1.1");
    if (!next) next = dlsym(RTLD_NEXT, "ibv_close_device");
    if (!next) { errno = ENOSYS; return -1; }
    return next(ctx);
}

int ibv_query_gid(struct ibv_context *ctx, uint8_t port, int index, union ibv_gid *gid) {
    if (inject("query_gid")) { record("query_gid\n"); errno = EIO; return -1; }
    int (*next)(struct ibv_context *, uint8_t, int, union ibv_gid *) = dlvsym(RTLD_NEXT, "ibv_query_gid", "IBVERBS_1.1");
    if (!next) next = dlsym(RTLD_NEXT, "ibv_query_gid");
    if (!next) { errno = ENOSYS; return -1; }
    return next(ctx, port, index, gid);
}
