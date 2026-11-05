.PHONY: all clean

all: banda-coletor

vmlinux.h:
	bpftool btf dump file /sys/kernel/btf/vmlinux format c > $@

banda.bpf.o: banda.bpf.c vmlinux.h
	clang -g -O2 -target bpf -D__TARGET_ARCH_x86 -ffile-prefix-map=$(CURDIR)=. -I. -c banda.bpf.c -o banda.bpf.o

banda.skel.h: banda.bpf.o
	bpftool gen skeleton banda.bpf.o > banda.skel.h

banda-coletor: banda-coletor.c banda.skel.h
	cc -O2 -Wall -Wextra -ffile-prefix-map=$(CURDIR)=. banda-coletor.c -o banda-coletor -lbpf -lelf -lz -lsqlite3

clean:
	rm -f vmlinux.h banda.bpf.o banda.skel.h banda-coletor
