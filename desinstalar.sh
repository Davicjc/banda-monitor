#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Erro: execute com sudo (ex.: sudo ./desinstalar.sh)." >&2
    exit 1
fi

MANTER_HISTORICO=0
for arg in "$@"; do
    case "$arg" in
        --manter-historico)
            MANTER_HISTORICO=1
            ;;
        *)
            echo "Uso: sudo ./desinstalar.sh [--manter-historico]" >&2
            exit 1
            ;;
    esac
done

echo "==> Parando e removendo serviços systemd..."
systemctl disable --now banda-coletor.service banda-monitor.socket banda-monitor.service 2>/dev/null || true
rm -f /etc/systemd/system/banda-coletor.service \
      /etc/systemd/system/banda-monitor.service \
      /etc/systemd/system/banda-monitor.socket
systemctl daemon-reload

echo "==> Removendo binários, servidor, ícones e atalhos..."
rm -rf /usr/local/lib/banda-monitor /run/banda-monitor

for dir_home in /home/* /root; do
    [ -d "$dir_home" ] || continue
    rm -f "$dir_home/.local/share/applications/banda.desktop"
    rm -rf "$dir_home/.local/share/cinnamon/applets/banda@banda-monitor"
done

if [ "$MANTER_HISTORICO" -eq 1 ]; then
    echo "==> Histórico mantido em /var/lib/banda-monitor e configuração em /etc/banda-monitor."
else
    printf "Deseja apagar também o histórico (/var/lib/banda-monitor) e configurações (/etc/banda-monitor)? [s/N]: "
    read -r resp || resp="n"
    case "$resp" in
        [sS]|[sS][iI][mM])
            rm -rf /var/lib/banda-monitor /etc/banda-monitor
            echo "==> Histórico e configurações removidos."
            ;;
        *)
            echo "==> Histórico mantido em /var/lib/banda-monitor."
            ;;
    esac
fi

echo "==> Banda desinstalado com sucesso."
