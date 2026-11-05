#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Erro: execute com sudo (ex.: sudo ./instalar.sh)." >&2
    exit 1
fi

if [ ! -e /sys/kernel/btf/vmlinux ]; then
    echo "Erro: /sys/kernel/btf/vmlinux não encontrado." >&2
    echo "Este coletor eBPF CO-RE exige um kernel Linux >= 5.8 compilado com suporte a BTF (CONFIG_DEBUG_INFO_BTF=y)." >&2
    exit 1
fi

DIR_PROJETO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

USUARIO_REAL="${SUDO_USER:-}"
if [ -z "$USUARIO_REAL" ] || [ "$USUARIO_REAL" = "root" ]; then
    USUARIO_REAL="$(logname 2>/dev/null || true)"
fi

HOME_USUARIO=""
GRUPO_USUARIO=""
UID_USUARIO=""
if [ -n "$USUARIO_REAL" ] && [ "$USUARIO_REAL" != "root" ] && id "$USUARIO_REAL" >/dev/null 2>&1; then
    HOME_USUARIO="$(getent passwd "$USUARIO_REAL" | cut -d: -f6)"
    GRUPO_USUARIO="$(id -gn "$USUARIO_REAL")"
    UID_USUARIO="$(id -u "$USUARIO_REAL")"
fi

if ! command -v apt-get >/dev/null 2>&1; then
    echo "Distribuição sem apt-get detectada." >&2
    echo "Instale manualmente os pacotes equivalentes: clang llvm bpftool libbpf-dev libsqlite3-dev libelf-dev zlib1g-dev make python3" >&2
    exit 1
fi

echo "==> Instalando dependências de compilação e execução..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y clang llvm libbpf-dev libsqlite3-dev libelf-dev zlib1g-dev make python3

if ! command -v bpftool >/dev/null 2>&1 || ! bpftool version >/dev/null 2>&1; then
    apt-get install -y bpftool 2>/dev/null || true
fi
if ! command -v bpftool >/dev/null 2>&1 || ! bpftool version >/dev/null 2>&1; then
    apt-get install -y linux-tools-common "linux-tools-$(uname -r)" 2>/dev/null || true
fi
if ! command -v bpftool >/dev/null 2>&1; then
    echo "Erro: bpftool não pôde ser instalado. Instale linux-tools-$(uname -r) ou bpftool." >&2
    exit 1
fi

echo "==> Compilando o coletor eBPF (coletor-c)..."
make -C "$DIR_PROJETO/coletor-c" clean all

echo "==> Instalando arquivos em /usr/local/lib/banda-monitor..."
install -d -m 0755 /usr/local/lib/banda-monitor
install -d -m 0755 /usr/local/lib/banda-monitor/servidor
install -d -m 0755 /usr/local/lib/banda-monitor/web
install -d -m 0755 /usr/local/lib/banda-monitor/icones
install -d -m 0755 /var/lib/banda-monitor
install -d -m 0755 /etc/banda-monitor

install -m 0755 "$DIR_PROJETO/coletor-c/banda-coletor" /usr/local/lib/banda-monitor/banda-coletor
install -m 0755 "$DIR_PROJETO/servidor/banda.py" /usr/local/lib/banda-monitor/servidor/banda.py
install -m 0644 "$DIR_PROJETO/web/index.html" /usr/local/lib/banda-monitor/web/index.html
install -m 0644 "$DIR_PROJETO/icones/"*.svg /usr/local/lib/banda-monitor/icones/

if [ ! -f /etc/banda-monitor/config.json ]; then
    cat > /etc/banda-monitor/config.json <<'EOF'
{
  "dias_minuto": 30,
  "dias_total": 0,
  "ignorar_interfaces": ["lo", "docker", "veth", "virbr", "br-"],
  "unidade": "bytes"
}
EOF
    chmod 0644 /etc/banda-monitor/config.json
fi

echo "==> Configurando serviços systemd..."
install -m 0644 "$DIR_PROJETO/sistema/banda-coletor.service" /etc/systemd/system/banda-coletor.service
install -m 0644 "$DIR_PROJETO/sistema/banda-monitor.service" /etc/systemd/system/banda-monitor.service
install -m 0644 "$DIR_PROJETO/sistema/banda-monitor.socket" /etc/systemd/system/banda-monitor.socket

systemctl daemon-reload
systemctl enable --now banda-coletor.service banda-monitor.socket
systemctl restart banda-coletor.service

if command -v google-chrome >/dev/null 2>&1; then
    EXEC_ABRIR="google-chrome --app=http://127.0.0.1:7788/ --window-size=1280,800"
elif command -v chromium >/dev/null 2>&1; then
    EXEC_ABRIR="chromium --app=http://127.0.0.1:7788/ --window-size=1280,800"
elif command -v chromium-browser >/dev/null 2>&1; then
    EXEC_ABRIR="chromium-browser --app=http://127.0.0.1:7788/ --window-size=1280,800"
else
    EXEC_ABRIR="xdg-open http://127.0.0.1:7788/"
fi

if [ -n "$HOME_USUARIO" ] && [ -d "$HOME_USUARIO" ]; then
    DIR_APPS="$HOME_USUARIO/.local/share/applications"
    install -d -m 0755 -o "$USUARIO_REAL" -g "$GRUPO_USUARIO" "$HOME_USUARIO/.local"
    install -d -m 0755 -o "$USUARIO_REAL" -g "$GRUPO_USUARIO" "$HOME_USUARIO/.local/share"
    install -d -m 0755 -o "$USUARIO_REAL" -g "$GRUPO_USUARIO" "$DIR_APPS"
    ARQ_DESKTOP="$DIR_APPS/banda.desktop"
    cat > "$ARQ_DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Banda — monitor de rede
Comment=Monitor de consumo de rede por aplicativo e destino
Exec=$EXEC_ABRIR
Icon=network-transmit-receive
Terminal=false
Categories=System;Monitor;Network;
StartupNotify=true
EOF
    chown "$USUARIO_REAL:$GRUPO_USUARIO" "$ARQ_DESKTOP"
    chmod 0644 "$ARQ_DESKTOP"

    if command -v cinnamon >/dev/null 2>&1 || [ "${XDG_CURRENT_DESKTOP:-}" = "X-Cinnamon" ] || [ -d "$HOME_USUARIO/.local/share/cinnamon" ]; then
        DIR_APPLET="$HOME_USUARIO/.local/share/cinnamon/applets/banda@banda-monitor"
        install -d -m 0755 -o "$USUARIO_REAL" -g "$GRUPO_USUARIO" "$HOME_USUARIO/.local/share/cinnamon"
        install -d -m 0755 -o "$USUARIO_REAL" -g "$GRUPO_USUARIO" "$HOME_USUARIO/.local/share/cinnamon/applets"
        install -d -m 0755 -o "$USUARIO_REAL" -g "$GRUPO_USUARIO" "$DIR_APPLET"
        install -m 0644 -o "$USUARIO_REAL" -g "$GRUPO_USUARIO" "$DIR_PROJETO/applet/banda@banda-monitor/applet.js" "$DIR_APPLET/applet.js"
        install -m 0644 -o "$USUARIO_REAL" -g "$GRUPO_USUARIO" "$DIR_PROJETO/applet/banda@banda-monitor/metadata.json" "$DIR_APPLET/metadata.json"

        DBUS_BUS="/run/user/$UID_USUARIO/bus"
        if [ -S "$DBUS_BUS" ] && command -v gsettings >/dev/null 2>&1; then
            ATUAIS="$(sudo -u "$USUARIO_REAL" DBUS_SESSION_BUS_ADDRESS="unix:path=$DBUS_BUS" gsettings get org.cinnamon enabled-applets 2>/dev/null || true)"
            if [ -n "$ATUAIS" ] && [[ "$ATUAIS" != *"banda@banda-monitor"* ]]; then
                NOVO_LIST="$(python3 - "$ATUAIS" <<'PYEOF'
import ast, sys
try:
    arr = ast.literal_eval(sys.argv[1])
except Exception:
    sys.exit(0)
if isinstance(arr, list):
    ids = [int(x.split(":")[4]) for x in arr if x.count(":") >= 4 and x.split(":")[4].isdigit()]
    arr.append("panel1:right:0:banda@banda-monitor:%d" % (max(ids, default=0) + 1))
    print(repr(arr))
PYEOF
)"
                if [ -n "$NOVO_LIST" ]; then
                    sudo -u "$USUARIO_REAL" DBUS_SESSION_BUS_ADDRESS="unix:path=$DBUS_BUS" gsettings set org.cinnamon enabled-applets "$NOVO_LIST" 2>/dev/null || true
                fi
            fi
        fi
        echo "==> Applet Cinnamon instalado em $DIR_APPLET"
        echo "    Caso não apareça no painel: clique com o botão direito no painel -> Applets -> ative 'Banda — Monitor de Rede'."
    fi
fi

echo "==> Instalação concluída! Acesse pelo menu de aplicativos ('Banda — monitor de rede') ou em http://127.0.0.1:7788/"
