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
