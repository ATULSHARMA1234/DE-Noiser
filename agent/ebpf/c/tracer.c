//go:build ignore

#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

char __license[] SEC("license") = "Dual MIT/GPL";

struct bpf_execve_event {
    int pid;
    char comm[16];
    char filename[128];
};

// Force clang to emit BTF for this struct
const struct bpf_execve_event *unused __attribute__((unused));

struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 256 * 1024); // 256 KB
} events SEC(".maps");

struct trace_event_raw_sys_enter_execve {
    unsigned long long unused;
    long syscall_nr;
    const char *filename;
    const char *const *argv;
    const char *const *envp;
};

SEC("tracepoint/syscalls/sys_enter_execve")
int tracepoint__syscalls__sys_enter_execve(struct trace_event_raw_sys_enter_execve *ctx) {
    struct bpf_execve_event *e;

    // Reserve space in ring buffer
    e = bpf_ringbuf_reserve(&events, sizeof(*e), 0);
    if (!e) {
        return 0;
    }

    // Get PID
    e->pid = bpf_get_current_pid_tgid() >> 32;

    // Get current process name
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    // Get filename executed
    bpf_probe_read_user_str(&e->filename, sizeof(e->filename), ctx->filename);

    // Submit to ring buffer
    bpf_ringbuf_submit(e, 0);

    return 0;
}
