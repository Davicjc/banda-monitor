#define _GNU_SOURCE
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#include <bpf/bpf.h>
#include <bpf/libbpf.h>
#include <sqlite3.h>

#include "banda.skel.h"

#define PADRAO_DB     "/var/lib/banda-monitor/historico.db"
#define PADRAO_AGORA  "/run/banda-monitor/agora.json"
#define PADRAO_CONFIG "/etc/banda-monitor/config.json"

#define MAX_CHAVE_APP   120
#define CACHE_PID_TAM   256
#define CACHE_PID_TTL   30

#define MAX_FLUXOS_2S   2048
#define HASH_TAM_2S     4096

#define MAX_FLUXOS_MIN  8192
#define HASH_TAM_MIN    16384

#define MAX_IFACES_IGN  32
#define TAM_IFACE_NOME  32

struct chave_fluxo {
	uint32_t pid;
	uint8_t proto;
	uint16_t porta;
	uint8_t familia;
	uint8_t ip[16];
};

struct valor_fluxo {
	uint64_t rx;
	uint64_t tx;
	char comm[16];
};

struct entrada_cache_pid {
	uint32_t pid;
	time_t expira;
	char app[MAX_CHAVE_APP + 1];
};

struct fluxo_agregado {
	char app[MAX_CHAVE_APP + 1];
	char ip[INET6_ADDRSTRLEN];
	uint16_t porta;
	uint8_t proto;
	uint64_t rx;
	uint64_t tx;
};

struct config_coletor {
	int dias_minuto;
	int dias_total;
	int num_ifaces;
	char ignorar_ifaces[MAX_IFACES_IGN][TAM_IFACE_NOME];
};

static volatile sig_atomic_t g_parar = 0;
static volatile sig_atomic_t g_recarregar = 0;

static struct entrada_cache_pid g_cache_pid[CACHE_PID_TAM];

static struct fluxo_agregado g_fluxos_2s[MAX_FLUXOS_2S];
static int g_hash_2s[HASH_TAM_2S];
static int g_num_fluxos_2s = 0;

static struct fluxo_agregado g_fluxos_min[MAX_FLUXOS_MIN];
static int g_hash_min[HASH_TAM_MIN];
static int g_num_fluxos_min = 0;
static uint64_t g_iface_min_rx = 0;
static uint64_t g_iface_min_tx = 0;

static struct config_coletor g_cfg;

static void tratador_sinal(int sig)
{
	if (sig == SIGINT || sig == SIGTERM)
		g_parar = 1;
	else if (sig == SIGHUP)
		g_recarregar = 1;
}

static void garantir_pasta_pai(const char *caminho)
{
	char tmp[512];
	snprintf(tmp, sizeof(tmp), "%s", caminho);
	char *barra = strrchr(tmp, '/');
	if (!barra || barra == tmp)
		return;
	*barra = '\0';

	for (char *p = tmp + 1; *p; p++) {
		if (*p == '/') {
			*p = '\0';
			mkdir(tmp, 0755);
			*p = '/';
		}
	}
	mkdir(tmp, 0755);
}

static ssize_t ler_arquivo_proc(const char *caminho, char *buf, size_t tam)
{
	if (tam == 0)
		return -1;
	int fd = open(caminho, O_RDONLY | O_CLOEXEC);
	if (fd < 0) {
		buf[0] = '\0';
		return -1;
	}
	ssize_t total = 0;
	while ((size_t)total < tam - 1) {
		ssize_t n = read(fd, buf + total, (tam - 1) - (size_t)total);
		if (n <= 0)
			break;
		total += n;
	}
	close(fd);
	buf[total] = '\0';
	return total;
}

static void ler_link_proc(const char *caminho, char *buf, size_t tam)
{
	if (tam == 0)
		return;
	ssize_t n = readlink(caminho, buf, tam - 1);
	if (n < 0) {
		buf[0] = '\0';
		return;
	}
	buf[n] = '\0';
	const char *suf = " (deleted)";
	size_t len_suf = strlen(suf);
	if ((size_t)n > len_suf && strcmp(buf + n - len_suf, suf) == 0)
		buf[n - len_suf] = '\0';
}

static bool eh_versao_numerica(const char *s)
{
	if (!s || !*s)
		return false;
	bool tem_ponto = false;
	for (const char *p = s; *p; p++) {
		if (*p == '.')
			tem_ponto = true;
		else if (!isdigit((unsigned char)*p))
			return false;
	}
	return tem_ponto;
}

static void obter_base_exe(uint32_t pid, char *exe_full, size_t tam_full, char *base_out, size_t tam_base)
{
	char caminho[64];
	snprintf(caminho, sizeof(caminho), "/proc/%u/exe", pid);
	ler_link_proc(caminho, exe_full, tam_full);
	if (exe_full[0] == '\0') {
		base_out[0] = '\0';
		return;
	}
	const char *b = strrchr(exe_full, '/');
	b = b ? (b + 1) : exe_full;
	snprintf(base_out, tam_base, "%.*s", (int)(tam_base - 1), b);

	if (eh_versao_numerica(base_out)) {
		char cmd[512];
		snprintf(caminho, sizeof(caminho), "/proc/%u/cmdline", pid);
		if (ler_arquivo_proc(caminho, cmd, sizeof(cmd)) > 0 && cmd[0] != '\0') {
			const char *cb = strrchr(cmd, '/');
			cb = cb ? (cb + 1) : cmd;
			if (*cb)
				snprintf(base_out, tam_base, "%.*s", (int)(tam_base - 1), cb);
		}
	}
}

static uint32_t obter_ppid(uint32_t pid)
{
	char caminho[64];
	char buf[2048];
	snprintf(caminho, sizeof(caminho), "/proc/%u/status", pid);
	if (ler_arquivo_proc(caminho, buf, sizeof(buf)) <= 0)
		return 0;
	const char *p = strstr(buf, "\nPPid:");
	if (!p) {
		if (strncmp(buf, "PPid:", 5) == 0)
			p = buf;
		else
			return 0;
	} else {
		p++;
	}
	p += 5;
	while (*p == ' ' || *p == '\t')
		p++;
	return (uint32_t)strtoul(p, NULL, 10);
}

static bool extrair_steam_jogo(const char *caminho, char *jogo_out, size_t tam)
{
	const char *marcador = "/steamapps/common/";
	const char *p = strstr(caminho, marcador);
	if (!p)
		return false;
	p += strlen(marcador);
	if (*p == '\0' || *p == '/')
		return false;
	const char *fim = strchr(p, '/');
	size_t len = fim ? (size_t)(fim - p) : strlen(p);
	if (len == 0)
		return false;
	if (len >= tam)
		len = tam - 1;
	memcpy(jogo_out, p, len);
	jogo_out[len] = '\0';
	return true;
}

static bool extrair_flatpak_id(const char *cgroup, char *id_out, size_t tam)
{
	const char *marcador = "app-flatpak-";
	const char *p = strstr(cgroup, marcador);
	if (!p)
		return false;
	const char *inicio = p + strlen(marcador);
	const char *fim_seg = inicio;
	while (*fim_seg && *fim_seg != '/' && *fim_seg != '\n' && *fim_seg != '\r')
		fim_seg++;

	const char *traco = NULL;
	for (const char *q = fim_seg - 1; q > inicio; q--) {
		if (*q == '-') {
			traco = q;
			if (isdigit((unsigned char)*(q + 1)))
				break;
		}
	}
	if (!traco || traco <= inicio)
		return false;

	size_t len = (size_t)(traco - inicio);
	if (len >= tam)
		len = tam - 1;
	memcpy(id_out, inicio, len);
	id_out[len] = '\0';
	return true;
}

static bool extrair_system_service(const char *cgroup, char *svc_out, size_t tam)
{
	const char *marcador = "/system.slice/";
	const char *p = strstr(cgroup, marcador);
	while (p) {
		const char *inicio = p + strlen(marcador);
		const char *fim = inicio;
		while (*fim && *fim != '/' && *fim != '\n' && *fim != '\r' && *fim != ' ')
			fim++;
		size_t len = (size_t)(fim - inicio);
		const char *suf = ".service";
		size_t len_suf = strlen(suf);
		if (len > len_suf && memcmp(inicio + len - len_suf, suf, len_suf) == 0) {
			size_t nome_len = len - len_suf;
			if (nome_len >= tam)
				nome_len = tam - 1;
			memcpy(svc_out, inicio, nome_len);
			svc_out[nome_len] = '\0';
			return true;
		}
		p = strstr(fim, marcador);
	}
	return false;
}

static void limpar_remoto_rclone(const char *arg, char *out, size_t tam)
{
	size_t j = 0;
	bool dentro_chaves = false;
	for (size_t i = 0; arg[i] != '\0' && j < tam - 1; i++) {
		if (arg[i] == ':')
			break;
		if (arg[i] == '{') {
			dentro_chaves = true;
			continue;
		}
		if (arg[i] == '}') {
			dentro_chaves = false;
			continue;
		}
		if (!dentro_chaves)
			out[j++] = arg[i];
	}
	out[j] = '\0';
}

static bool resolver_chave_pid(uint32_t pid, const char *comm_bpf, char *out, size_t tam_out)
{
	char exe_full[512];
	char base_exe[128];
	obter_base_exe(pid, exe_full, sizeof(exe_full), base_exe, sizeof(base_exe));

	char caminho[64];
	char cgroup[2048];
	snprintf(caminho, sizeof(caminho), "/proc/%u/cgroup", pid);
	ssize_t n_cg = ler_arquivo_proc(caminho, cgroup, sizeof(cgroup));

	/* 1. exe basename rclone e cmdline contém mount -> nuvem:<remoto> */
	if (strcmp(base_exe, "rclone") == 0) {
		char cmd[4096];
		snprintf(caminho, sizeof(caminho), "/proc/%u/cmdline", pid);
		ssize_t n = ler_arquivo_proc(caminho, cmd, sizeof(cmd));
		if (n > 0) {
			bool achou_mount = false;
			ssize_t pos = 0;
			while (pos < n) {
				const char *tok = cmd + pos;
				size_t tlen = strlen(tok);
				if (tlen == 0) {
					pos++;
					continue;
				}
				if (!achou_mount) {
					if (strcmp(tok, "mount") == 0)
						achou_mount = true;
				} else if (tok[0] != '-') {
					char remoto[128];
					limpar_remoto_rclone(tok, remoto, sizeof(remoto));
					if (remoto[0] != '\0') {
						snprintf(out, tam_out, "nuvem:%.110s", remoto);
						return true;
					}
					break;
				}
				pos += (ssize_t)tlen + 1;
			}
		}
	}

	/* 2. exe basename restic ou cgroup contém /system.slice/backup-sistema.service -> backup */
	if (strcmp(base_exe, "restic") == 0 ||
	    strstr(cgroup, "/system.slice/backup-sistema.service") != NULL) {
		snprintf(out, tam_out, "backup");
		return true;
	}

	/* 3. cgroup contém app-flatpak-<ID>- -> flatpak:<ID> */
	char fp_id[128];
	if (extrair_flatpak_id(cgroup, fp_id, sizeof(fp_id))) {
		snprintf(out, tam_out, "flatpak:%.110s", fp_id);
		return true;
	}

	/* 4. exe ou cwd contém /steamapps/common/<Jogo>/ -> steam:<Jogo> */
	char jogo[128];
	if (extrair_steam_jogo(exe_full, jogo, sizeof(jogo))) {
		snprintf(out, tam_out, "steam:%.110s", jogo);
		return true;
	}
	char cwd[512];
	snprintf(caminho, sizeof(caminho), "/proc/%u/cwd", pid);
	ler_link_proc(caminho, cwd, sizeof(cwd));
	if (extrair_steam_jogo(cwd, jogo, sizeof(jogo))) {
		snprintf(out, tam_out, "steam:%.110s", jogo);
		return true;
	}

	/* 5. sobe pelos pais enquanto o pai tiver o mesmo exe basename; cgroup do topo em /system.slice/<x>.service -> svc:<x> */
	uint32_t topo_pid = pid;
	if (base_exe[0] != '\0') {
		for (int hop = 0; hop < 16; hop++) {
			uint32_t ppid = obter_ppid(topo_pid);
			if (ppid <= 1 || ppid == topo_pid)
				break;
			char p_exe[512];
			char p_base[128];
			obter_base_exe(ppid, p_exe, sizeof(p_exe), p_base, sizeof(p_base));
			if (p_base[0] != '\0' && strcmp(p_base, base_exe) == 0)
				topo_pid = ppid;
			else
				break;
		}
	}

	const char *cg_topo = cgroup;
	char cgroup_topo_buf[2048];
	if (topo_pid != pid) {
		snprintf(caminho, sizeof(caminho), "/proc/%u/cgroup", topo_pid);
		if (ler_arquivo_proc(caminho, cgroup_topo_buf, sizeof(cgroup_topo_buf)) > 0)
			cg_topo = cgroup_topo_buf;
	}

	if (strstr(cg_topo, "/system.slice/backup-sistema.service") != NULL) {
		snprintf(out, tam_out, "backup");
		return true;
	}

	char svc[128];
	if (extrair_system_service(cg_topo, svc, sizeof(svc))) {
		snprintf(out, tam_out, "svc:%.110s", svc);
		return true;
	}

	/* 6. senão -> exe:<basename do exe>; sem acesso ao exe -> comm:<comm> */
	if (base_exe[0] != '\0') {
		snprintf(out, tam_out, "exe:%.110s", base_exe);
		return true;
	}

	char comm[128];
	snprintf(caminho, sizeof(caminho), "/proc/%u/comm", pid);
	if (ler_arquivo_proc(caminho, comm, sizeof(comm)) > 0) {
		char *nl = strpbrk(comm, "\r\n");
		if (nl)
			*nl = '\0';
		if (comm[0] != '\0') {
			snprintf(out, tam_out, "comm:%.110s", comm);
			return n_cg >= 0;
		}
	}

	if (comm_bpf && comm_bpf[0] != '\0') {
		char comm_limpo[17];
		memcpy(comm_limpo, comm_bpf, 16);
		comm_limpo[16] = '\0';
		char *nl = strpbrk(comm_limpo, "\r\n");
		if (nl)
			*nl = '\0';
		if (comm_limpo[0] != '\0') {
			snprintf(out, tam_out, "comm:%s", comm_limpo);
			return false;
		}
	}

	snprintf(out, tam_out, "comm:pid-%u", pid);
	return false;
}

static const char *obter_app_cache(uint32_t pid, const char *comm_bpf, time_t agora_mono)
{
	uint32_t base = (pid * 2654435761u) & (CACHE_PID_TAM - 1);
	int slot_livre = -1;
	time_t menor_exp = 0;

	for (uint32_t i = 0; i < 8; i++) {
		uint32_t idx = (base + i) & (CACHE_PID_TAM - 1);
		if (g_cache_pid[idx].pid == pid && g_cache_pid[idx].expira > agora_mono)
			return g_cache_pid[idx].app;
		if (slot_livre < 0 || g_cache_pid[idx].pid == 0 ||
		    g_cache_pid[idx].expira <= agora_mono ||
		    g_cache_pid[idx].expira < menor_exp) {
			slot_livre = (int)idx;
			menor_exp = g_cache_pid[idx].expira;
			if (g_cache_pid[idx].pid == 0 || g_cache_pid[idx].expira <= agora_mono)
				break;
		}
	}

	int idx = (slot_livre >= 0) ? slot_livre : (int)base;
	bool proc_vivo = resolver_chave_pid(pid, comm_bpf, g_cache_pid[idx].app, sizeof(g_cache_pid[idx].app));
	g_cache_pid[idx].pid = pid;
	g_cache_pid[idx].expira = proc_vivo ? (agora_mono + CACHE_PID_TTL) : agora_mono;
	return g_cache_pid[idx].app;
}

static uint32_t hash_fluxo(const char *app, uint8_t proto, uint16_t porta, const char *ip)
{
	uint32_t h = 2166136261u;
	for (const unsigned char *p = (const unsigned char *)app; *p; p++)
		h = (h ^ *p) * 16777619u;
	h = (h ^ proto) * 16777619u;
	h = (h ^ (porta & 0xff)) * 16777619u;
	h = (h ^ (porta >> 8)) * 16777619u;
	for (const unsigned char *p = (const unsigned char *)ip; *p; p++)
		h = (h ^ *p) * 16777619u;
	return h;
}

static void somar_agregado(struct fluxo_agregado *arr, int max_arr, int * n_arr,
                           int *tabela_hash, int tam_hash,
                           const char *app, uint8_t proto, uint16_t porta, const char *ip,
                           uint64_t rx, uint64_t tx)
{
	uint32_t h = hash_fluxo(app, proto, porta, ip);
	uint32_t mask = (uint32_t)(tam_hash - 1);

	for (int i = 0; i < tam_hash; i++) {
		uint32_t pos = (h + (uint32_t)i) & mask;
		int idx = tabela_hash[pos];
		if (idx < 0) {
			if (*n_arr >= max_arr)
				return;
			int novo = (*n_arr)++;
			tabela_hash[pos] = novo;
			snprintf(arr[novo].app, sizeof(arr[novo].app), "%s", app);
			snprintf(arr[novo].ip, sizeof(arr[novo].ip), "%s", ip);
			arr[novo].porta = porta;
			arr[novo].proto = proto;
			arr[novo].rx = rx;
			arr[novo].tx = tx;
			return;
		}
		if (arr[idx].proto == proto && arr[idx].porta == porta &&
		    strcmp(arr[idx].ip, ip) == 0 && strcmp(arr[idx].app, app) == 0) {
			arr[idx].rx += rx;
			arr[idx].tx += tx;
			return;
		}
	}
}

static void carregar_config(const char *caminho)
{
	g_cfg.dias_minuto = 30;
	g_cfg.dias_total = 0; /* 0 = para sempre */
	const char *padrao_ifaces[] = {"lo", "docker", "veth", "virbr", "br-"};
	g_cfg.num_ifaces = 5;
	for (int i = 0; i < 5; i++)
		snprintf(g_cfg.ignorar_ifaces[i], TAM_IFACE_NOME, "%s", padrao_ifaces[i]);

	char buf[8192];
	if (ler_arquivo_proc(caminho, buf, sizeof(buf)) <= 0)
		return;

	const char *p_min = strstr(buf, "\"dias_minuto\"");
	if (p_min) {
		const char *c = strchr(p_min, ':');
		if (c) {
			long v = strtol(c + 1, NULL, 10);
			if (v >= 1 && v <= 3650)
				g_cfg.dias_minuto = (int)v;
		}
	}

	const char *p_tot = strstr(buf, "\"dias_total\"");
	if (p_tot) {
		const char *c = strchr(p_tot, ':');
		if (c) {
			long v = strtol(c + 1, NULL, 10);
			if (v >= 0 && v <= 36500)
				g_cfg.dias_total = (int)v;
		}
	}

	const char *p_if = strstr(buf, "\"ignorar_interfaces\"");
	if (p_if) {
		const char *col_ab = strchr(p_if, '[');
		const char *col_fe = col_ab ? strchr(col_ab, ']') : NULL;
		if (col_ab && col_fe && col_fe > col_ab) {
			int n = 0;
			const char *q = col_ab + 1;
			while (q < col_fe && n < MAX_IFACES_IGN) {
				const char *asp1 = memchr(q, '"', (size_t)(col_fe - q));
				if (!asp1)
					break;
				const char *asp2 = memchr(asp1 + 1, '"', (size_t)(col_fe - (asp1 + 1)));
				if (!asp2)
					break;
				size_t len = (size_t)(asp2 - (asp1 + 1));
				if (len > 0) {
					if (len >= TAM_IFACE_NOME)
						len = TAM_IFACE_NOME - 1;
					memcpy(g_cfg.ignorar_ifaces[n], asp1 + 1, len);
					g_cfg.ignorar_ifaces[n][len] = '\0';
					n++;
				}
				q = asp2 + 1;
			}
			g_cfg.num_ifaces = n;
		}
	}
}

static bool iface_ignorada(const char *iface)
{
	for (int i = 0; i < g_cfg.num_ifaces; i++) {
		const char *pref = g_cfg.ignorar_ifaces[i];
		size_t len = strlen(pref);
		if (len > 0 && strncmp(iface, pref, len) == 0)
			return true;
	}
	return false;
}

static void ler_bytes_interfaces(uint64_t *rx_out, uint64_t *tx_out)
{
	*rx_out = 0;
	*tx_out = 0;
	FILE *f = fopen("/proc/net/dev", "r");
	if (!f)
		return;

	char linha[512];
	int num_linha = 0;
	while (fgets(linha, sizeof(linha), f)) {
		num_linha++;
		if (num_linha <= 2)
			continue;
		char *dois_pontos = strchr(linha, ':');
		if (!dois_pontos)
			continue;
		*dois_pontos = '\0';
		char *iface = linha;
		while (*iface == ' ' || *iface == '\t')
			iface++;
		char *fim_if = iface + strlen(iface);
		while (fim_if > iface && (*(fim_if - 1) == ' ' || *(fim_if - 1) == '\t'))
			*(--fim_if) = '\0';

		if (iface_ignorada(iface))
			continue;

		char *p = dois_pontos + 1;
		uint64_t cols[9] = {0};
		int c = 0;
		while (c < 9) {
			while (*p == ' ' || *p == '\t')
				p++;
			if (*p == '\0' || *p == '\n')
				break;
			char *prox = NULL;
			cols[c] = strtoull(p, &prox, 10);
			if (prox == p)
				break;
			p = prox;
			c++;
		}
		if (c >= 9) {
			*rx_out += cols[0];
			*tx_out += cols[8];
		}
	}
	fclose(f);
}

static bool formatar_e_filtrar_ip(const struct chave_fluxo *k, char *ip_str, size_t tam)
{
	if (k->familia == 2) {
		if (k->ip[0] == 127)
			return false;
		if (k->ip[0] == 0 && k->ip[1] == 0 && k->ip[2] == 0 && k->ip[3] == 0 && k->porta == 0)
			return false;
		return inet_ntop(AF_INET, k->ip, ip_str, (socklen_t)tam) != NULL;
	} else if (k->familia == 10) {
		bool zeros_10 = true;
		for (int i = 0; i < 10; i++) {
			if (k->ip[i] != 0) {
				zeros_10 = false;
				break;
			}
		}
		/* IPv4 mapeado em IPv6 ::ffff:a.b.c.d */
		if (zeros_10 && k->ip[10] == 0xff && k->ip[11] == 0xff) {
			if (k->ip[12] == 127)
				return false;
			return inet_ntop(AF_INET, k->ip + 12, ip_str, (socklen_t)tam) != NULL;
		}
		/* Loopback ::1 ou :: não conectado com porta 0 */
		if (zeros_10 && k->ip[10] == 0 && k->ip[11] == 0 &&
		    k->ip[12] == 0 && k->ip[13] == 0 && k->ip[14] == 0) {
			if (k->ip[15] == 1)
				return false;
			if (k->ip[15] == 0 && k->porta == 0)
				return false;
		}
		return inet_ntop(AF_INET6, k->ip, ip_str, (socklen_t)tam) != NULL;
	}
	return false;
}

static void escrever_json_string(FILE *f, const char *s)
{
	fputc('"', f);
	for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
		if (*p == '"' || *p == '\\') {
			fputc('\\', f);
			fputc(*p, f);
		} else if (*p >= 0x20) {
			fputc(*p, f);
		}
	}
	fputc('"', f);
}

static void gravar_agora_json(const char *caminho_agora, time_t t_epoch, double dt,
                              uint64_t iface_rx, uint64_t iface_tx)
{
	char tmp[540];
	snprintf(tmp, sizeof(tmp), "%s.tmp", caminho_agora);

	FILE *f = fopen(tmp, "w");
	if (!f)
		return;

	fprintf(f, "{\"t\": %lld, \"dt\": %.2f,\n", (long long)t_epoch, dt);
	fprintf(f, " \"iface\": {\"rx\": %llu, \"tx\": %llu},\n",
	        (unsigned long long)iface_rx, (unsigned long long)iface_tx);
	fprintf(f, " \"fluxos\": [");

	for (int i = 0; i < g_num_fluxos_2s; i++) {
		const struct fluxo_agregado *fl = &g_fluxos_2s[i];
		if (i > 0)
			fprintf(f, ", ");
		fprintf(f, "{\"app\": ");
		escrever_json_string(f, fl->app);
		fprintf(f, ", \"proto\": %u, \"porta\": %u, \"ip\": ",
		        (unsigned)fl->proto, (unsigned)fl->porta);
		escrever_json_string(f, fl->ip);
		fprintf(f, ", \"rx\": %llu, \"tx\": %llu}",
		        (unsigned long long)fl->rx, (unsigned long long)fl->tx);
	}

	fprintf(f, "]}\n");
	fflush(f);
	fchmod(fileno(f), 0644);
	fclose(f);
	rename(tmp, caminho_agora);
}

static int inicializar_db(const char *caminho_db, sqlite3 **db_out,
                          sqlite3_stmt **stmt_min, sqlite3_stmt **stmt_iface)
{
	garantir_pasta_pai(caminho_db);
	if (sqlite3_open(caminho_db, db_out) != SQLITE_OK)
		return -1;

	const char *sql_init =
		"PRAGMA journal_mode=WAL;"
		"PRAGMA synchronous=NORMAL;"
		"PRAGMA busy_timeout=5000;"
		"CREATE TABLE IF NOT EXISTS minuto (ts INTEGER NOT NULL, app TEXT NOT NULL, proto INTEGER NOT NULL, porta INTEGER NOT NULL, ip TEXT NOT NULL, rx INTEGER NOT NULL, tx INTEGER NOT NULL);"
		"CREATE INDEX IF NOT EXISTS minuto_ts ON minuto(ts);"
		"CREATE TABLE IF NOT EXISTS hora   (ts INTEGER NOT NULL, app TEXT NOT NULL, proto INTEGER NOT NULL, porta INTEGER NOT NULL, ip TEXT NOT NULL, rx INTEGER NOT NULL, tx INTEGER NOT NULL);"
		"CREATE INDEX IF NOT EXISTS hora_ts ON hora(ts);"
		"CREATE TABLE IF NOT EXISTS iface_minuto (ts INTEGER PRIMARY KEY, rx INTEGER NOT NULL, tx INTEGER NOT NULL);"
		"CREATE TABLE IF NOT EXISTS iface_hora   (ts INTEGER PRIMARY KEY, rx INTEGER NOT NULL, tx INTEGER NOT NULL);";

	char *err = NULL;
	if (sqlite3_exec(*db_out, sql_init, NULL, NULL, &err) != SQLITE_OK) {
		sqlite3_free(err);
		return -1;
	}

	const char *q_min = "INSERT INTO minuto (ts, app, proto, porta, ip, rx, tx) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7);";
	if (sqlite3_prepare_v2(*db_out, q_min, -1, stmt_min, NULL) != SQLITE_OK)
		return -1;

	const char *q_if = "INSERT INTO iface_minuto (ts, rx, tx) VALUES (?1, ?2, ?3) "
	                   "ON CONFLICT(ts) DO UPDATE SET rx = iface_minuto.rx + excluded.rx, tx = iface_minuto.tx + excluded.tx;";
	if (sqlite3_prepare_v2(*db_out, q_if, -1, stmt_iface, NULL) != SQLITE_OK)
		return -1;

	return 0;
}

static void gravar_minuto_sqlite(sqlite3 *db, sqlite3_stmt *stmt_min, sqlite3_stmt *stmt_iface, time_t ts_minuto)
{
	if (g_num_fluxos_min == 0 && g_iface_min_rx == 0 && g_iface_min_tx == 0)
		return;

	if (sqlite3_exec(db, "BEGIN IMMEDIATE;", NULL, NULL, NULL) != SQLITE_OK)
		return;

	for (int i = 0; i < g_num_fluxos_min; i++) {
		const struct fluxo_agregado *fl = &g_fluxos_min[i];
		sqlite3_reset(stmt_min);
		sqlite3_bind_int64(stmt_min, 1, (sqlite3_int64)ts_minuto);
		sqlite3_bind_text(stmt_min, 2, fl->app, -1, SQLITE_STATIC);
		sqlite3_bind_int(stmt_min, 3, (int)fl->proto);
		sqlite3_bind_int(stmt_min, 4, (int)fl->porta);
		sqlite3_bind_text(stmt_min, 5, fl->ip, -1, SQLITE_STATIC);
		sqlite3_bind_int64(stmt_min, 6, (sqlite3_int64)fl->rx);
		sqlite3_bind_int64(stmt_min, 7, (sqlite3_int64)fl->tx);
		sqlite3_step(stmt_min);
	}

	sqlite3_reset(stmt_iface);
	sqlite3_bind_int64(stmt_iface, 1, (sqlite3_int64)ts_minuto);
	sqlite3_bind_int64(stmt_iface, 2, (sqlite3_int64)g_iface_min_rx);
	sqlite3_bind_int64(stmt_iface, 3, (sqlite3_int64)g_iface_min_tx);
	sqlite3_step(stmt_iface);

	sqlite3_exec(db, "COMMIT;", NULL, NULL, NULL);

	g_num_fluxos_min = 0;
	g_iface_min_rx = 0;
	g_iface_min_tx = 0;
	memset(g_hash_min, -1, sizeof(g_hash_min));
}

static void aplicar_retencao(sqlite3 *db, time_t agora)
{
	long long corte_min = (long long)(((agora - (time_t)g_cfg.dias_minuto * 86400) / 3600) * 3600);
	long long corte_tot = g_cfg.dias_total > 0 ? (long long)(((agora - (time_t)g_cfg.dias_total * 86400) / 3600) * 3600) : 0; /* 0 = nunca apaga */

	char sql[2048];
	snprintf(sql, sizeof(sql),
		"BEGIN IMMEDIATE;"
		"INSERT INTO hora (ts, app, proto, porta, ip, rx, tx) "
		"SELECT (ts / 3600) * 3600 AS h_ts, app, proto, porta, ip, SUM(rx), SUM(tx) "
		"FROM minuto WHERE ts < %lld GROUP BY h_ts, app, proto, porta, ip;"
		"DELETE FROM minuto WHERE ts < %lld;"
		"INSERT INTO iface_hora (ts, rx, tx) "
		"SELECT (ts / 3600) * 3600 AS h_ts, SUM(rx), SUM(tx) "
		"FROM iface_minuto WHERE ts < %lld GROUP BY h_ts "
		"ON CONFLICT(ts) DO UPDATE SET rx = iface_hora.rx + excluded.rx, tx = iface_hora.tx + excluded.tx;"
		"DELETE FROM iface_minuto WHERE ts < %lld;"
		"DELETE FROM hora WHERE ts < %lld;"
		"DELETE FROM iface_hora WHERE ts < %lld;"
		"COMMIT;"
		"PRAGMA optimize;",
		corte_min, corte_min, corte_min, corte_min, corte_tot, corte_tot);

	sqlite3_exec(db, sql, NULL, NULL, NULL);

	/* VACUUM só 1x por semana (guarda hora epoch em PRAGMA user_version) */
	long hora_atual = (long)(agora / 3600);
	long ult_vacuum = 0;
	sqlite3_stmt *st = NULL;
	if (sqlite3_prepare_v2(db, "PRAGMA user_version;", -1, &st, NULL) == SQLITE_OK) {
		if (sqlite3_step(st) == SQLITE_ROW)
			ult_vacuum = sqlite3_column_int(st, 0);
		sqlite3_finalize(st);
	}

	if (ult_vacuum == 0) {
		snprintf(sql, sizeof(sql), "PRAGMA user_version = %ld;", hora_atual);
		sqlite3_exec(db, sql, NULL, NULL, NULL);
	} else if (hora_atual - ult_vacuum >= 7 * 24) {
		sqlite3_exec(db, "VACUUM;", NULL, NULL, NULL);
		snprintf(sql, sizeof(sql), "PRAGMA user_version = %ld;", hora_atual);
		sqlite3_exec(db, sql, NULL, NULL, NULL);
	}
}

static void processar_item_mapa(const struct chave_fluxo *k, const struct valor_fluxo *v, time_t agora_mono)
{
	if (v->rx == 0 && v->tx == 0)
		return;
	char ip_str[INET6_ADDRSTRLEN];
	if (!formatar_e_filtrar_ip(k, ip_str, sizeof(ip_str)))
		return;
	const char *app = obter_app_cache(k->pid, v->comm, agora_mono);

	somar_agregado(g_fluxos_2s, MAX_FLUXOS_2S, &g_num_fluxos_2s,
	               g_hash_2s, HASH_TAM_2S, app, k->proto, k->porta, ip_str, v->rx, v->tx);
	somar_agregado(g_fluxos_min, MAX_FLUXOS_MIN, &g_num_fluxos_min,
	               g_hash_min, HASH_TAM_MIN, app, k->proto, k->porta, ip_str, v->rx, v->tx);
}

static void drenar_mapa_bpf(int map_fd, time_t agora_mono)
{
	struct chave_fluxo prev_key, key;
	bool tem_prev = false;

	while (bpf_map_get_next_key(map_fd, tem_prev ? &prev_key : NULL, &key) == 0) {
		if (tem_prev) {
			struct valor_fluxo val;
			if (bpf_map_lookup_and_delete_elem(map_fd, &prev_key, &val) == 0) {
				processar_item_mapa(&prev_key, &val, agora_mono);
			} else if (bpf_map_lookup_elem(map_fd, &prev_key, &val) == 0) {
				bpf_map_delete_elem(map_fd, &prev_key);
				processar_item_mapa(&prev_key, &val, agora_mono);
			}
		}
		prev_key = key;
		tem_prev = true;
	}
	if (tem_prev) {
		struct valor_fluxo val;
		if (bpf_map_lookup_and_delete_elem(map_fd, &prev_key, &val) == 0) {
			processar_item_mapa(&prev_key, &val, agora_mono);
		} else if (bpf_map_lookup_elem(map_fd, &prev_key, &val) == 0) {
			bpf_map_delete_elem(map_fd, &prev_key);
			processar_item_mapa(&prev_key, &val, agora_mono);
		}
	}
}

int main(int argc, char **argv)
{
	const char *caminho_db = PADRAO_DB;
	const char *caminho_agora = PADRAO_AGORA;
	const char *caminho_config = PADRAO_CONFIG;

	for (int i = 1; i < argc; i++) {
		if (strcmp(argv[i], "--teste-chaves") == 0) {
			for (int j = i + 1; j < argc; j++) {
				uint32_t pid = (uint32_t)strtoul(argv[j], NULL, 10);
				char chave[MAX_CHAVE_APP + 1];
				resolver_chave_pid(pid, NULL, chave, sizeof(chave));
				printf("%u: %s\n", pid, chave);
			}
			return 0;
		} else if (strcmp(argv[i], "--db") == 0 && i + 1 < argc) {
			caminho_db = argv[++i];
		} else if (strcmp(argv[i], "--agora") == 0 && i + 1 < argc) {
			caminho_agora = argv[++i];
		} else if (strcmp(argv[i], "--config") == 0 && i + 1 < argc) {
			caminho_config = argv[++i];
		} else {
			fprintf(stderr, "Uso: %s [--db ARQ] [--agora ARQ] [--config ARQ] [--teste-chaves PID...]\n", argv[0]);
			return 1;
		}
	}

	struct sigaction sa;
	memset(&sa, 0, sizeof(sa));
	sa.sa_handler = tratador_sinal;
	sigaction(SIGINT, &sa, NULL);
	sigaction(SIGTERM, &sa, NULL);
	sigaction(SIGHUP, &sa, NULL);

	carregar_config(caminho_config);
	garantir_pasta_pai(caminho_agora);

	sqlite3 *db = NULL;
	sqlite3_stmt *stmt_min = NULL;
	sqlite3_stmt *stmt_iface = NULL;
	if (inicializar_db(caminho_db, &db, &stmt_min, &stmt_iface) != 0) {
		fprintf(stderr, "Erro ao inicializar SQLite em %s\n", caminho_db);
		return 1;
	}

	time_t agora_epoch = time(NULL);
	aplicar_retencao(db, agora_epoch);
	time_t ult_retencao = agora_epoch;

	struct banda_bpf *skel = banda_bpf__open_and_load();
	if (!skel) {
		fprintf(stderr, "Erro ao abrir/carregar programa eBPF\n");
		sqlite3_finalize(stmt_min);
		sqlite3_finalize(stmt_iface);
		sqlite3_close(db);
		return 1;
	}

	if (banda_bpf__attach(skel) != 0) {
		fprintf(stderr, "Erro ao anexar sondas eBPF\n");
		banda_bpf__destroy(skel);
		sqlite3_finalize(stmt_min);
		sqlite3_finalize(stmt_iface);
		sqlite3_close(db);
		return 1;
	}

	int map_fd = bpf_map__fd(skel->maps.fluxos);

	memset(g_hash_min, -1, sizeof(g_hash_min));
	uint64_t ult_iface_rx = 0, ult_iface_tx = 0;
	ler_bytes_interfaces(&ult_iface_rx, &ult_iface_tx);

	struct timespec ts_ant;
	clock_gettime(CLOCK_MONOTONIC, &ts_ant);
	time_t min_atual = (time(NULL) / 60) * 60;

	while (!g_parar) {
		struct timespec req = {.tv_sec = 2, .tv_nsec = 0};
		clock_nanosleep(CLOCK_MONOTONIC, 0, &req, NULL);
		if (g_parar && g_num_fluxos_min == 0) {
			/* Ain't skipping final drain before exit */
		}

		if (g_recarregar) {
			g_recarregar = 0;
			carregar_config(caminho_config);
		}

		struct timespec ts_agora;
		clock_gettime(CLOCK_MONOTONIC, &ts_agora);
		double dt = (double)(ts_agora.tv_sec - ts_ant.tv_sec) +
		            (double)(ts_agora.tv_nsec - ts_ant.tv_nsec) / 1e9;
		if (dt <= 0.0)
			dt = 2.0;
		ts_ant = ts_agora;

		uint64_t rx_dev = 0, tx_dev = 0;
		ler_bytes_interfaces(&rx_dev, &tx_dev);
		uint64_t d_rx = (rx_dev >= ult_iface_rx) ? (rx_dev - ult_iface_rx) : 0;
		uint64_t d_tx = (tx_dev >= ult_iface_tx) ? (tx_dev - ult_iface_tx) : 0;
		ult_iface_rx = rx_dev;
		ult_iface_tx = tx_dev;

		g_iface_min_rx += d_rx;
		g_iface_min_tx += d_tx;

		g_num_fluxos_2s = 0;
		memset(g_hash_2s, -1, sizeof(g_hash_2s));
		drenar_mapa_bpf(map_fd, ts_agora.tv_sec);

		agora_epoch = time(NULL);
		gravar_agora_json(caminho_agora, agora_epoch, dt, d_rx, d_tx);

		time_t min_novo = (agora_epoch / 60) * 60;
		if (min_novo > min_atual) {
			gravar_minuto_sqlite(db, stmt_min, stmt_iface, min_atual);
			min_atual = min_novo;
		}

		if (agora_epoch - ult_retencao >= 86400) {
			aplicar_retencao(db, agora_epoch);
			ult_retencao = agora_epoch;
		}
	}

	gravar_minuto_sqlite(db, stmt_min, stmt_iface, min_atual);

	banda_bpf__destroy(skel);
	sqlite3_finalize(stmt_min);
	sqlite3_finalize(stmt_iface);
	sqlite3_close(db);
	return 0;
}
