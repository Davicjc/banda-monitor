# Arquitetura e contrato entre o coletor (C) e o servidor/interface (Python + web)

Projeto "Banda" — monitor de rede por aplicativo para Linux (Debian/Ubuntu/Mint, kernel ≥ 5.8 com BTF).

## Processos
1. **`banda-coletor`** (C + libbpf CO-RE, root, SEMPRE ligado, meta: < 5 MB RSS, ~0% CPU).
   - Sondas eBPF (`tcp_sendmsg`, `tcp_cleanup_rbuf`, `udp_sendmsg`/`udpv6_sendmsg`, `udp_recvmsg`/`udpv6_recvmsg` kprobe+kretprobe). Mapa hash no kernel com chave `{u32 pid; u8 proto; u16 porta; u8 familia; u8 ip[16]}` e valor `{u64 rx; u64 tx; char comm[16]}` (`comm` preenchido via `bpf_get_current_comm`, último escritor vence).
   - A cada **2 s**: lê e zera o mapa, resolve a **chave de app** de cada pid (regras abaixo, cache por pid 30 s), soma no acumulador do minuto e grava **`/run/banda-monitor/agora.json`** (escrita atômica: arquivo `.tmp` + `rename`), permissões 0644.
   - A cada **60 s** (no minuto cheio): grava o acumulado no SQLite e zera o acumulador.
   - Uma vez por dia (e na inicialização): consolida e limpa (ver retenção).
   - Lê `/etc/banda-monitor/config.json` na inicialização e ao receber **SIGHUP**.
2. **`banda.py`** (servidor da janela, só quando a janela está aberta): ativado sob demanda via `systemd` socket (`127.0.0.1:7788`), lê `agora.json` a cada 2 s para o tempo real e o SQLite (somente leitura) para o histórico, encerrando sozinho após 60 s sem clientes SSE conectados.

## `/run/banda-monitor/agora.json`
```json
{"t": 1793897256, "dt": 2.0,
 "iface": {"rx": 123456, "tx": 7890},
 "fluxos": [{"app": "exe:google-chrome", "proto": 17, "porta": 443, "ip": "142.250.78.142", "rx": 1234, "tx": 567}]}
```
- `t` epoch (s), `dt` segundos reais desde a leitura anterior, `rx/tx` em **bytes no intervalo** (não por segundo). `iface` = bytes no intervalo somando `/proc/net/dev` exceto `lo`, `docker*`, `veth*`, `virbr*`, `br-*`. Loopback (`127/8`, `::1`) é descartado no coletor.

## Chave de app (`app`), calculada no coletor a partir de `/proc/PID/{exe,cmdline,cgroup,status}` e `comm` do eBPF
Em ordem:
1. exe basename `rclone` e cmdline contém `mount` → `nuvem:<remoto>` (o 1º argumento após `mount`, sem os `:` finais e sem `{...}`; ex. `nuvem:gdrive`, `nuvem:iclouddrive,service=photos`).
2. exe basename `restic` ou cgroup contém `/system.slice/backup-sistema.service` → `backup`.
3. cgroup contém `app-flatpak-<ID>-` → `flatpak:<ID>`.
4. exe ou cwd contém `/steamapps/common/<Jogo>/` → `steam:<Jogo>`.
5. sobe pelos pais (`PPid`) enquanto o pai tiver o mesmo exe basename; cgroup do topo em `/system.slice/<x>.service` → `svc:<x>`.
6. senão → `exe:<basename do exe>`; se não der para ler `/proc/PID` (ex. processo curto-vivo que já terminou antes da resolução), usa `comm:<comm>` obtido de `/proc/PID/comm` ou do `comm` capturado no kernel pelo eBPF (ex. `comm:curl`).
Chaves têm no máximo 120 bytes.

## SQLite `/var/lib/banda-monitor/historico.db` (WAL, criado pelo coletor)
```sql
CREATE TABLE IF NOT EXISTS minuto (ts INTEGER NOT NULL, app TEXT NOT NULL, proto INTEGER NOT NULL, porta INTEGER NOT NULL, ip TEXT NOT NULL, rx INTEGER NOT NULL, tx INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS minuto_ts ON minuto(ts);
CREATE TABLE IF NOT EXISTS hora   (ts INTEGER NOT NULL, app TEXT NOT NULL, proto INTEGER NOT NULL, porta INTEGER NOT NULL, ip TEXT NOT NULL, rx INTEGER NOT NULL, tx INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS hora_ts ON hora(ts);
CREATE TABLE IF NOT EXISTS iface_minuto (ts INTEGER PRIMARY KEY, rx INTEGER NOT NULL, tx INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS iface_hora   (ts INTEGER PRIMARY KEY, rx INTEGER NOT NULL, tx INTEGER NOT NULL);
```
- `ts` = início do minuto/hora (epoch, múltiplo de 60/3600). Valores em **bytes** no período.
- Linhas de `minuto` com `ts` do mesmo minuto e mesma `(app,proto,porta,ip)` são únicas por gravação (o coletor agrega antes).
- **Retenção** (config): `dias_minuto` (padrão 30): o que for mais velho vai para `hora` (`SUM` agrupado por `hora/app/proto/porta/ip`) e sai de `minuto`; `dias_total` (padrão 0 = para sempre): quando maior que 0, apaga de `hora` o que for mais velho. Mesmo para `iface_*`. Depois `PRAGMA optimize`; `VACUUM` só 1x por semana.

## `/etc/banda-monitor/config.json`
```json
{"dias_minuto": 30, "dias_total": 0, "ignorar_interfaces": ["lo","docker","veth","virbr","br-"], "unidade": "bytes"}
```
`unidade` (`"bytes"` | `"bits"`) é só da interface. O servidor (root) grava esse arquivo pela rota `POST /api/config` e manda `SIGHUP` ao coletor (`systemctl kill -s HUP banda-coletor.service`).
