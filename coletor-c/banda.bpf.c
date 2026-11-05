#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wmissing-declarations"
#include "vmlinux.h"
#pragma clang diagnostic pop
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_endian.h>

struct chave_fluxo {
	__u32 pid;
	__u8 proto;
	__u16 porta;
	__u8 familia;
	__u8 ip[16];
};

struct valor_fluxo {
	__u64 rx;
	__u64 tx;
	char comm[16];
};

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 16384);
	__type(key, struct chave_fluxo);
	__type(value, struct valor_fluxo);
} fluxos SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 4096);
	__type(key, __u32);
	__type(value, __u64);
} usk SEC(".maps");

static __always_inline int contabilizar(struct sock *sk, __u8 proto, __u64 bytes, bool is_tx)
{
	if (!sk || bytes == 0)
		return 0;

	__u16 fam = BPF_CORE_READ(sk, __sk_common.skc_family);
	if (fam != 2 && fam != 10)
		return 0;

	struct chave_fluxo k;
	__builtin_memset(&k, 0, sizeof(k));
	k.pid = (__u32)(bpf_get_current_pid_tgid() >> 32);
	k.proto = proto;
	k.porta = bpf_ntohs(BPF_CORE_READ(sk, __sk_common.skc_dport));
	k.familia = (__u8)fam;

	if (fam == 2) {
		__u32 daddr = BPF_CORE_READ(sk, __sk_common.skc_daddr);
		__builtin_memcpy(k.ip, &daddr, 4);
		if (k.ip[0] == 127)
			return 0;
	} else {
		BPF_CORE_READ_INTO(&k.ip, sk, __sk_common.skc_v6_daddr.in6_u.u6_addr8);
	}

	struct valor_fluxo *v = bpf_map_lookup_elem(&fluxos, &k);
	if (!v) {
		struct valor_fluxo zero = {};
		bpf_map_update_elem(&fluxos, &k, &zero, BPF_NOEXIST);
		v = bpf_map_lookup_elem(&fluxos, &k);
		if (!v)
			return 0;
	}

	bpf_get_current_comm(&v->comm, sizeof(v->comm));

	if (is_tx)
		__sync_fetch_and_add(&v->tx, bytes);
	else
		__sync_fetch_and_add(&v->rx, bytes);

	return 0;
}

SEC("kprobe/tcp_sendmsg")
int BPF_KPROBE(kprobe_tcp_sendmsg, struct sock *sk, struct msghdr *msg, size_t size)
{
	(void)msg;
	return contabilizar(sk, 6, (__u64)size, true);
}

SEC("kprobe/tcp_cleanup_rbuf")
int BPF_KPROBE(kprobe_tcp_cleanup_rbuf, struct sock *sk, int copied)
{
	if (copied <= 0)
		return 0;
	return contabilizar(sk, 6, (__u64)copied, false);
}

SEC("kprobe/udp_sendmsg")
int BPF_KPROBE(kprobe_udp_sendmsg, struct sock *sk, struct msghdr *msg, size_t len)
{
	(void)msg;
	return contabilizar(sk, 17, (__u64)len, true);
}

SEC("kprobe/udpv6_sendmsg")
int BPF_KPROBE(kprobe_udpv6_sendmsg, struct sock *sk, struct msghdr *msg, size_t len)
{
	(void)msg;
	return contabilizar(sk, 17, (__u64)len, true);
}

SEC("kprobe/udp_recvmsg")
int BPF_KPROBE(kprobe_udp_recvmsg, struct sock *sk)
{
	__u32 tid = (__u32)bpf_get_current_pid_tgid();
	__u64 sk_ptr = (__u64)sk;
	bpf_map_update_elem(&usk, &tid, &sk_ptr, BPF_ANY);
	return 0;
}

SEC("kprobe/udpv6_recvmsg")
int BPF_KPROBE(kprobe_udpv6_recvmsg, struct sock *sk)
{
	__u32 tid = (__u32)bpf_get_current_pid_tgid();
	__u64 sk_ptr = (__u64)sk;
	bpf_map_update_elem(&usk, &tid, &sk_ptr, BPF_ANY);
	return 0;
}

SEC("kretprobe/udp_recvmsg")
int BPF_KRETPROBE(kretprobe_udp_recvmsg, int ret)
{
	__u32 tid = (__u32)bpf_get_current_pid_tgid();
	__u64 *sk_ptr = bpf_map_lookup_elem(&usk, &tid);
	if (!sk_ptr)
		return 0;
	struct sock *sk = (struct sock *)(*sk_ptr);
	bpf_map_delete_elem(&usk, &tid);
	if (ret > 0)
		contabilizar(sk, 17, (__u64)ret, false);
	return 0;
}

SEC("kretprobe/udpv6_recvmsg")
int BPF_KRETPROBE(kretprobe_udpv6_recvmsg, int ret)
{
	__u32 tid = (__u32)bpf_get_current_pid_tgid();
	__u64 *sk_ptr = bpf_map_lookup_elem(&usk, &tid);
	if (!sk_ptr)
		return 0;
	struct sock *sk = (struct sock *)(*sk_ptr);
	bpf_map_delete_elem(&usk, &tid);
	if (ret > 0)
		contabilizar(sk, 17, (__u64)ret, false);
	return 0;
}

char LICENSE[] SEC("license") = "GPL";
