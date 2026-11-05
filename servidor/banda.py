#!/usr/bin/env python3
import argparse
import collections
import concurrent.futures
import glob
import http.server
import ipaddress
import json
import math
import mimetypes
import os
import pathlib
import queue
import random
import re
import select
import shlex
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse

ICON_THEMES = ["ePapirus", "Papirus", "Mint-Y", "hicolor", "Adwaita"]
ICON_SIZES = ["scalable", "64x64", "48x48", "128x128", "256x256"]
ICONES_PROPRIOS_DIRS = [
    pathlib.Path("/usr/local/lib/banda-monitor/icones"),
    pathlib.Path(__file__).resolve().parent.parent / "icones",
]


def _obter_desktop_dirs() -> list[str]:
    dirs = [
        "/usr/share/applications",
        "/var/lib/flatpak/exports/share/applications",
    ]
    dirs.extend(sorted(glob.glob("/home/*/.local/share/applications")))
    dirs.extend(sorted(glob.glob("/home/*/.local/share/flatpak/exports/share/applications")))
    dirs.append("/root/.local/share/applications")
    dirs.append("/root/.local/share/flatpak/exports/share/applications")
    return dirs


def encontrar_icone_proprio(arq: str) -> str | None:
    for d in ICONES_PROPRIOS_DIRS:
        p = d / arq
        if p.is_file():
            return str(p)
    return None

REDES_LOCAIS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

CONFIG_PADRAO = {
    "dias_minuto": 30,
    "dias_total": 0,  # 0 = para sempre
    "ignorar_interfaces": ["lo", "docker", "veth", "virbr", "br-"],
    "unidade": "bytes",
}

ORG_RULES = [
    (("googlevideo", "youtube", "ytimg"), "YouTube"),
    (("google", "1e100.net", "gstatic", "googleapis"), "Google"),
    (("cloudfront",), "AWS CloudFront"),
    (("amazonaws",), "AWS"),
    (("akamai",), "Akamai"),
    (("fbcdn", "facebook"), "Meta"),
    (("whatsapp",), "WhatsApp"),
    (("steamcontent", "steamserver", "valve", "steampowered"), "Steam"),
    (("discord",), "Discord"),
    (("microsoft", "msn", "live.com", "office", "azure", "windows"), "Microsoft"),
    (("apple", "icloud", "aaplimg"), "Apple"),
    (("cloudflare", "one.one.one.one"), "Cloudflare"),
    (("anthropic", "claude.ai"), "Anthropic"),
    (("github",), "GitHub"),
    (("spotify", "scdn.co"), "Spotify"),
    (("twitch", "ttvnw"), "Twitch"),
    (("netflix", "nflx"), "Netflix"),
    (("telegram",), "Telegram"),
    (("bitwarden",), "Bitwarden"),
]

# Faixas conhecidas: dão a organização quando o IP não tem nome (sem DNS reverso nem no cache)
FAIXAS_ORG = [(ipaddress.ip_network(n), org) for n, org in [
    ("172.217.0.0/16", "Google"), ("142.250.0.0/15", "Google"), ("216.58.192.0/19", "Google"),
    ("74.125.0.0/16", "Google"), ("64.233.160.0/19", "Google"), ("173.194.0.0/16", "Google"),
    ("209.85.128.0/17", "Google"), ("108.177.0.0/17", "Google"), ("172.253.0.0/16", "Google"),
    ("34.64.0.0/10", "Google Cloud"), ("35.184.0.0/13", "Google Cloud"), ("2800:3f0::/32", "Google"), ("2a00:1450::/32", "Google"), ("2607:f8b0::/32", "Google"),
    ("104.16.0.0/13", "Cloudflare"), ("104.24.0.0/14", "Cloudflare"), ("172.64.0.0/13", "Cloudflare"),
    ("162.158.0.0/15", "Cloudflare"), ("188.114.96.0/20", "Cloudflare"), ("1.1.1.0/24", "Cloudflare"), ("1.0.0.0/24", "Cloudflare"), ("2606:4700::/32", "Cloudflare"),
    ("151.101.0.0/16", "Fastly"), ("199.232.0.0/16", "Fastly"), ("2a04:4e40::/32", "Fastly"),
    ("160.79.104.0/23", "Anthropic"),
    ("157.240.0.0/16", "Meta"), ("31.13.24.0/21", "Meta"), ("31.13.64.0/18", "Meta"), ("2a03:2880::/32", "Meta"),
    ("155.133.224.0/19", "Steam"), ("162.254.192.0/21", "Steam"), ("205.196.6.0/24", "Steam"), ("185.25.180.0/22", "Steam"),
    ("13.64.0.0/11", "Microsoft"), ("20.0.0.0/8", "Microsoft"), ("40.64.0.0/10", "Microsoft"), ("52.96.0.0/12", "Microsoft"), ("2603:1000::/24", "Microsoft"),
    ("17.0.0.0/8", "Apple"), ("2620:149::/32", "Apple"),
    ("13.32.0.0/15", "AWS CloudFront"), ("13.224.0.0/14", "AWS CloudFront"), ("18.64.0.0/14", "AWS CloudFront"), ("99.84.0.0/16", "AWS CloudFront"),
    ("3.0.0.0/9", "AWS"), ("52.0.0.0/11", "AWS"), ("54.64.0.0/11", "AWS"), ("54.144.0.0/12", "AWS"), ("18.128.0.0/9", "AWS"),
    ("140.82.112.0/20", "GitHub"), ("185.199.108.0/22", "GitHub"),
    ("35.186.224.0/20", "Spotify"),
    ("149.154.160.0/20", "Telegram"),
]]

HOSTS_DEMO = {
    "142.250.78.142": ("rr2---sn-4g5ednsd.googlevideo.com", "YouTube"),
    "104.16.132.229": ("cdn.cloudflare.net", "Cloudflare"),
    "160.79.104.20": ("api.claude.ai", "Anthropic"),
    "155.133.226.70": ("gru.valve.net", "Steam"),
    "155.133.226.71": ("cdn.steamcontent.com", "Steam"),
    "162.159.135.232": ("brazil1042.discord.media", "Discord"),
    "162.159.136.232": ("webrtc.discord.gg", "Discord"),
    "35.186.224.25": ("audio-fa.scdn.co", "Spotify"),
    "172.217.28.14": ("www.googleapis.com", "Google"),
    "172.217.29.46": ("storage.googleapis.com", "Google"),
    "149.154.167.99": ("dc4.telegram.org", "Telegram"),
    "13.224.161.90": ("d111111abcdef8.cloudfront.net", "AWS CloudFront"),
    "140.82.112.4": ("github.com", "GitHub"),
    "1.1.1.1": ("one.one.one.one", "Cloudflare"),
    "1.0.0.1": ("one.one.one.one", "Cloudflare"),
    "151.101.1.69": ("vault.bitwarden.com", "Bitwarden"),
}

FLUXOS_DEMO_VIVO = [
    ("exe:google-chrome", 17, 443, "142.250.78.142", 420_000, 18_000),
    ("exe:google-chrome", 6, 443, "104.16.132.229", 95_000, 12_000),
    ("exe:google-chrome", 6, 443, "160.79.104.20", 34_000, 15_000),
    ("steam:Counter-Strike 2", 17, 27025, "155.133.226.70", 145_000, 48_000),
    ("flatpak:com.discordapp.Discord", 17, 443, "162.159.135.232", 88_000, 36_000),
    ("flatpak:com.discordapp.Discord", 17, 3478, "162.159.136.232", 16_000, 14_000),
    ("exe:spotify", 6, 443, "35.186.224.25", 64_000, 3_200),
    ("nuvem:gdrive", 6, 443, "172.217.28.14", 42_000, 165_000),
    ("backup", 6, 443, "172.217.29.46", 4_500, 290_000),
    ("flatpak:org.telegram.desktop", 6, 443, "149.154.167.99", 38_000, 9_500),
    ("exe:firefox", 6, 443, "13.224.161.90", 52_000, 6_400),
    ("svc:systemd-resolved", 17, 53, "1.1.1.1", 3_800, 1_900),
]


def org_por_faixa(ip_str: str) -> str | None:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return None
    for rede, org in FAIXAS_ORG:
        if ip.version == rede.version and ip in rede:
            return org
    return None


def servico_por_porta(porta: int, proto_num: int) -> str:
    proto = "TCP" if proto_num == 6 else ("UDP" if proto_num == 17 else str(proto_num))
    if porta == 443 and proto == "TCP":
        return "HTTPS"
    if porta == 443 and proto == "UDP":
        return "QUIC"
    if porta == 80:
        return "HTTP"
    if porta == 53:
        return "DNS"
    if porta == 853:
        return "DNS-TLS"
    if porta == 22:
        return "SSH"
    if porta == 123:
        return "NTP"
    if porta == 8291:
        return "WinBox"
    if porta == 51820:
        return "WireGuard"
    if porta in (3478, 3479):
        return "STUN"
    if porta == 5353:
        return "mDNS"
    if 27000 <= porta <= 27100:
        return "Steam (jogo)"
    if porta == 993:
        return "IMAPS"
    if porta in (587, 465):
        return "SMTP"
    return f"{proto} {porta}"


def _parse_desktop_file(caminho: str) -> dict | None:
    try:
        with open(caminho, "r", encoding="utf-8", errors="replace") as f:
            linhas = f.readlines()
    except OSError:
        return None

    in_entry = False
    dados = {}
    for lin in linhas:
        s = lin.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("["):
            if s == "[Desktop Entry]":
                in_entry = True
                continue
            elif in_entry:
                break
            else:
                continue
        if in_entry and "=" in s:
            k, v = s.split("=", 1)
            dados[k.strip()] = v.strip()

    if not dados:
        return None

    base_file = os.path.basename(caminho)
    file_id = base_file[:-8] if base_file.endswith(".desktop") else base_file
    nome = dados.get("Name[pt_BR]") or dados.get("Name") or file_id
    wm_class = dados.get("StartupWMClass", "").strip()
    icon = dados.get("Icon", "").strip()
    no_display = dados.get("NoDisplay", "").strip().lower() == "true"

    exec_str = dados.get("Exec", "").strip()
    exec_base = ""
    if exec_str:
        try:
            tokens = shlex.split(exec_str)
        except ValueError:
            tokens = exec_str.split()
        idx = 0
        while idx < len(tokens):
            t = tokens[idx]
            b = os.path.basename(t)
            if b == "env":
                idx += 1
                while idx < len(tokens) and "=" in tokens[idx] and not tokens[idx].startswith("-"):
                    idx += 1
                continue
            if b == "flatpak" and idx + 1 < len(tokens) and tokens[idx + 1] == "run":
                idx += 2
                while idx < len(tokens) and tokens[idx].startswith("-"):
                    idx += 1
                continue
            exec_base = b
            break

    return {
        "file_id": file_id,
        "nome": nome,
        "wm_class": wm_class,
        "exec_base": exec_base,
        "icon": icon,
        "no_display": no_display,
    }


class CatalogoApps:
    def __init__(self):
        self.desktops: list[dict] = []
        self.icon_paths: dict[str, str] = {}
        self.icon_search_cache: dict[str, str | None] = {}
        self.app_cache: dict[str, dict] = {}
        self._carregar_desktops()
        for ch_padrao in (
            "nuvem:gdrive",
            "nuvem:onedrive",
            "nuvem:iclouddrive",
            "nuvem:iclouddrive,service=photos",
            "backup",
        ):
            self.descrever_app(ch_padrao)

    def _carregar_desktops(self):
        normais = []
        ocultos = []
        for d in _obter_desktop_dirs():
            if not os.path.isdir(d):
                continue
            try:
                nomes = sorted(os.listdir(d))
            except OSError:
                continue
            for fn in nomes:
                if not fn.endswith(".desktop"):
                    continue
                info = _parse_desktop_file(os.path.join(d, fn))
                if not info:
                    continue
                if info["no_display"]:
                    ocultos.append(info)
                else:
                    normais.append(info)
        self.desktops = normais + ocultos

    def resolver_caminho_icone(self, icon_name: str) -> str | None:
        if not icon_name:
            return None
        if icon_name in self.icon_search_cache:
            return self.icon_search_cache[icon_name]

        if os.path.isabs(icon_name):
            if os.path.isfile(icon_name):
                self.icon_search_cache[icon_name] = icon_name
                return icon_name
            self.icon_search_cache[icon_name] = None
            return None

        stem = icon_name
        for ext in (".svg", ".png"):
            if stem.endswith(ext):
                stem = stem[: -len(ext)]
                break

        candidatos = []
        for tema in ICON_THEMES:
            for tam in ICON_SIZES:
                base_dir = f"/usr/share/icons/{tema}/{tam}/apps"
                candidatos.append(f"{base_dir}/{stem}.svg")
                candidatos.append(f"{base_dir}/{stem}.png")
            if tema == "Mint-Y":
                for tam_mint in ("64", "48", "128", "256", "32"):
                    base_dir = f"/usr/share/icons/Mint-Y/apps/{tam_mint}"
                    candidatos.append(f"{base_dir}/{stem}.svg")
                    candidatos.append(f"{base_dir}/{stem}.png")

        dirs_user_icons = ["/var/lib/flatpak/exports/share/icons/hicolor"]
        dirs_user_icons.extend(sorted(glob.glob("/home/*/.local/share/flatpak/exports/share/icons/hicolor")))
        dirs_user_icons.append("/root/.local/share/flatpak/exports/share/icons/hicolor")
        dirs_user_icons.extend(sorted(glob.glob("/home/*/.local/share/icons/hicolor")))
        dirs_user_icons.append("/root/.local/share/icons/hicolor")

        for base_fp in dirs_user_icons:
            if os.path.isdir(base_fp):
                for tam in ("scalable", "64x64", "128x128", "256x256", "48x48", "512x512", "32x32"):
                    candidatos.append(f"{base_fp}/{tam}/apps/{stem}.svg")
                    candidatos.append(f"{base_fp}/{tam}/apps/{stem}.png")
                for p in glob.glob(f"{base_fp}/*/apps/{stem}.svg") + glob.glob(f"{base_fp}/*/apps/{stem}.png"):
                    candidatos.append(p)

        candidatos.append(f"/usr/share/pixmaps/{stem}.svg")
        candidatos.append(f"/usr/share/pixmaps/{stem}.png")
        candidatos.append(f"/usr/share/pixmaps/{icon_name}")

        for c in candidatos:
            if os.path.isfile(c):
                self.icon_search_cache[icon_name] = c
                return c

        self.icon_search_cache[icon_name] = None
        return None

    def registrar_icone(self, chave_app: str, icon_name: str | None, fallback_icon: str | None = None) -> str | None:
        caminho = self.resolver_caminho_icone(icon_name or "")
        if not caminho and fallback_icon:
            caminho = self.resolver_caminho_icone(fallback_icon)
        if caminho:
            id_limpo = re.sub(r"[^A-Za-z0-9_.-]", "_", chave_app)
            self.icon_paths[id_limpo] = caminho
            return f"/icone/{id_limpo}"
        return None

    def buscar_desktop(self, chave: str, is_flatpak: bool = False) -> dict | None:
        if not chave:
            return None
        cl = chave.lower()
        aliases = {
            "chrome": "google-chrome",
            "google-chrome": "google-chrome",
            "google-chrome-stable": "google-chrome",
            "code": "com.microsoft.vscode",
        }
        alvo = aliases.get(cl, cl)

        if is_flatpak:
            for d in self.desktops:
                if d["file_id"].lower() == cl:
                    return d

        for d in self.desktops:
            if not d["no_display"] and d["file_id"].lower() == alvo:
                return d
        for d in self.desktops:
            if not d["no_display"] and d["wm_class"] and d["wm_class"].lower() == alvo:
                return d
        for d in self.desktops:
            if not d["no_display"] and d["exec_base"]:
                eb = d["exec_base"].lower()
                if eb == alvo or eb == cl or eb.startswith(alvo + "-"):
                    return d
        for d in self.desktops:
            if not d["no_display"]:
                fid = d["file_id"].lower()
                if fid == alvo or fid == cl or fid.split(".")[-1] == cl:
                    return d
        return None

    def descrever_app(self, chave: str) -> dict:
        chave = (chave or "").strip()
        if not chave:
            chave = "comm:desconhecido"
        if chave in self.app_cache:
            return self.app_cache[chave]

        if chave == "outros":
            res = {
                "id": "outros",
                "nome": "Rede (cabeçalhos TCP/IP, ACKs, ICMP)",
                "icone": None,
            }
            self.app_cache[chave] = res
            return res

        if chave.startswith("nuvem:"):
            remoto = chave[6:].strip()
            if remoto == "gdrive":
                nome = "Google Drive"
                arq = "google-drive.svg"
            elif remoto == "onedrive":
                nome = "OneDrive"
                arq = "onedrive.svg"
            elif remoto == "iclouddrive,service=photos" or (remoto.startswith("iclouddrive") and "photos" in remoto):
                nome = "iCloud Fotos"
                arq = "icloud-fotos.svg"
            elif remoto.startswith("iclouddrive"):
                nome = "iCloud Drive"
                arq = "icloud-drive.svg"
            else:
                nome = f"Nuvem · {remoto}"
                arq = None

            caminho_nuvem = encontrar_icone_proprio(arq) if arq else None
            icone = self.registrar_icone(chave, caminho_nuvem, "folder-cloud")
            res = {"id": chave, "nome": nome, "icone": icone}
            self.app_cache[chave] = res
            return res

        if chave == "backup":
            icone = self.registrar_icone(
                chave,
                encontrar_icone_proprio("backup.svg"),
                "drive-harddisk",
            )
            res = {"id": chave, "nome": "Backup do PC", "icone": icone}
            self.app_cache[chave] = res
            return res

        if chave.startswith("flatpak:"):
            fp_id = chave[8:].strip()
            desk = self.buscar_desktop(fp_id, is_flatpak=True)
            if desk:
                nome = desk["nome"]
                icone = self.registrar_icone(chave, desk["icon"], fp_id)
            else:
                partes = fp_id.split(".")
                nome = partes[-1].capitalize() if partes else fp_id
                icone = self.registrar_icone(chave, fp_id)
            res = {"id": chave, "nome": nome, "icone": icone}
            self.app_cache[chave] = res
            return res

        if chave.startswith("steam:"):
            jogo = chave[6:].strip() or "Steam"
            icone = self.registrar_icone(chave, "steam")
            res = {"id": chave, "nome": jogo, "icone": icone}
            self.app_cache[chave] = res
            return res

        if chave.startswith("svc:"):
            svc = chave[4:].strip()
            nome = f"Sistema · {svc}"
            icone = self.registrar_icone(chave, svc)
            res = {"id": chave, "nome": nome, "icone": icone}
            self.app_cache[chave] = res
            return res

        if chave.startswith("exe:"):
            base = chave[4:].strip()
            desk = self.buscar_desktop(base)
            if desk:
                nome = desk["nome"]
                icone = self.registrar_icone(chave, desk["icon"], base)
            else:
                nome = base
                icone = self.registrar_icone(chave, base)
            res = {"id": chave, "nome": nome, "icone": icone}
            self.app_cache[chave] = res
            return res

        if chave.startswith("comm:"):
            c = chave[5:].strip()
            icone = self.registrar_icone(chave, c)
            res = {"id": chave, "nome": c, "icone": icone}
            self.app_cache[chave] = res
            return res

        desk = self.buscar_desktop(chave)
        if desk:
            nome = desk["nome"]
            icone = self.registrar_icone(chave, desk["icon"], chave)
        else:
            nome = chave
            icone = self.registrar_icone(chave, chave)
        res = {"id": chave, "nome": nome, "icone": icone}
        self.app_cache[chave] = res
        return res


_CATALOGO_GLOBAL = CatalogoApps()


def descrever_app(chave: str) -> dict:
    return _CATALOGO_GLOBAL.descrever_app(chave)


class ResolvedorDestinos:
    def __init__(self, modo_demo: bool = False):
        self.modo_demo = modo_demo
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self.cache_host: dict[str, str | None] = {}
        self.pendentes: dict[str, concurrent.futures.Future] = {}
        self.lock = threading.Lock()
        self.cache_dns: dict[str, str] = {}
        self.cache_dns_ts = 0.0

    def _ler_cache_resolved(self):
        if self.modo_demo:
            return
        if time.monotonic() - self.cache_dns_ts < 10:
            return
        self.cache_dns_ts = time.monotonic()
        try:
            out = subprocess.run(
                ["resolvectl", "show-cache"],
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout
        except Exception:
            return
        cname = {}
        novo = {}
        for linha in re.sub(r"\x1b\[[0-9;]*m", "", out).splitlines():
            partes = linha.split()
            if len(partes) == 4 and partes[1] == "IN":
                nome, tipo, valor = (
                    partes[0].rstrip(".").lower(),
                    partes[2],
                    partes[3].rstrip(".").lower(),
                )
                if tipo in ("A", "AAAA"):
                    novo.setdefault(valor, nome)
                elif tipo == "CNAME":
                    cname[valor] = nome
        for ip, nome in list(novo.items()):
            vistos = 0
            while nome in cname and vistos < 5:
                nome = cname[nome]
                vistos += 1
            novo[ip] = nome
        self.cache_dns.update(novo)

    def eh_loopback(self, ip_str: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_str)
            if ip.is_loopback:
                return True
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped and ip.ipv4_mapped.is_loopback:
                return True
            return False
        except ValueError:
            return ip_str.startswith("127.") or ip_str == "::1"

    def eh_rede_local(self, ip_str: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_str)
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            for net in REDES_LOCAIS:
                if ip.version == net.version and ip in net:
                    return True
            return False
        except ValueError:
            return False

    def _lookup(self, ip_str: str) -> str | None:
        try:
            host, _, _ = socket.gethostbyaddr(ip_str)
            return host.rstrip(".").lower() if host else None
        except Exception:
            return None

    def obter_host_e_org(self, ip_str: str, resolver_async: bool = True) -> tuple[str, str]:
        if ip_str in ("0.0.0.0", "::"):
            return ("Vários", "Vários")
        if self.modo_demo and ip_str in HOSTS_DEMO:
            return HOSTS_DEMO[ip_str]
        if self.eh_rede_local(ip_str):
            return ("Rede local", "Rede local")

        host = None
        with self.lock:
            if ip_str in self.cache_host:
                host = self.cache_host[ip_str]
            elif resolver_async and not self.modo_demo:
                fut = self.pendentes.get(ip_str)
                if fut is None and len(self.pendentes) < 32:
                    fut = self.executor.submit(self._lookup, ip_str)
                    self.pendentes[ip_str] = fut
                if fut and fut.done():
                    try:
                        host = fut.result()
                    except Exception:
                        host = None
                    self.cache_host[ip_str] = host
                    self.pendentes.pop(ip_str, None)

        if not host and not self.modo_demo:
            self._ler_cache_resolved()
            host = self.cache_dns.get(ip_str)
        if not host and ip_str in HOSTS_DEMO:
            return HOSTS_DEMO[ip_str]
        if not host:
            return (ip_str, org_por_faixa(ip_str) or ip_str)

        org = self.classificar_org(host, ip_str)
        return (host, org)

    def aguardar_rapido(self, timeout: float = 0.15):
        if self.modo_demo:
            return
        with self.lock:
            futs = list(self.pendentes.values())
        if futs:
            concurrent.futures.wait(futs, timeout=timeout)
            with self.lock:
                for ip_str, fut in list(self.pendentes.items()):
                    if fut.done():
                        try:
                            self.cache_host[ip_str] = fut.result()
                        except Exception:
                            self.cache_host[ip_str] = None
                        self.pendentes.pop(ip_str, None)

    @staticmethod
    def classificar_org(host: str, ip_fallback: str) -> str:
        hl = host.lower().strip(".")
        if not hl:
            return org_por_faixa(ip_fallback) or ip_fallback
        for patterns, org_name in ORG_RULES:
            for p in patterns:
                if p in hl:
                    return org_name
        faixa = org_por_faixa(ip_fallback)
        if faixa:
            return faixa
        partes = hl.split(".")
        if len(partes) >= 3 and hl.endswith((".com.br", ".net.br", ".org.br")):
            return ".".join(partes[-3:])
        if len(partes) >= 2:
            return ".".join(partes[-2:])
        return hl


class MonitorBanda:
    def __init__(self, args):
        self.args = args
        self.catalogo = _CATALOGO_GLOBAL
        self.destinos = ResolvedorDestinos(modo_demo=bool(args.demo))
        self.historico = collections.deque(maxlen=450)
        self.lock = threading.Lock()
        self.clientes_sse: list[queue.Queue] = []
        self.ultimo_sse_ativo = time.monotonic()
        self.coleta_iniciada = False
        self.encerrando = False
        self.ultimo_t_agora: int | None = None
        self.rng_demo = random.Random()

        if args.demo:
            self._preencher_historico_demo()
            self.garantir_coleta()

    def garantir_coleta(self):
        with self.lock:
            if self.coleta_iniciada:
                return
            self.coleta_iniciada = True

        alvo = self._loop_demo if self.args.demo else self._loop_agora
        t = threading.Thread(target=alvo, daemon=True)
        t.start()

    def _gerar_bruto_demo(self, t_epoch: int) -> dict:
        fluxos = []
        soma_rx = 0
        soma_tx = 0
        fase = (t_epoch % 300) / 300.0 * math.pi * 2.0
        for idx, (app, proto, porta, ip, rx_b, tx_b) in enumerate(FLUXOS_DEMO_VIVO):
            osc = 0.65 + 0.45 * math.sin(fase + idx * 0.7)
            ruido = self.rng_demo.uniform(0.75, 1.25)
            rx = max(0, int(rx_b * osc * ruido))
            tx = max(0, int(tx_b * osc * ruido))
            if rx == 0 and tx == 0:
                continue
            fluxos.append({
                "app": app,
                "proto": proto,
                "porta": porta,
                "ip": ip,
                "rx": rx,
                "tx": tx,
            })
            soma_rx += rx
            soma_tx += tx

        over_rx = int(soma_rx * self.rng_demo.uniform(0.03, 0.06))
        over_tx = int(soma_tx * self.rng_demo.uniform(0.03, 0.06))
        return {
            "t": t_epoch,
            "dt": 2.0,
            "iface": {"rx": soma_rx + over_rx, "tx": soma_tx + over_tx},
            "fluxos": fluxos,
        }

    def _preencher_historico_demo(self):
        agora = int(time.time())
        inicio = agora - 60 * 2
        for t_ep in range(inicio, agora, 2):
            bruto = self._gerar_bruto_demo(t_ep)
            am = self._processar_agora_obj(bruto)
            if am:
                self.historico.append(am)

    def _processar_agora_obj(self, obj: dict) -> dict | None:
        try:
            t_val = int(obj.get("t") or 0)
            dt = float(obj.get("dt") or 2.0)
        except (ValueError, TypeError):
            return None

        if t_val <= 0 or dt < 0.5 or dt > 10.0:
            return None

        iface = obj.get("iface") or {}
        iface_rx_s = max(0, int(round(float(iface.get("rx") or 0) / dt)))
        iface_tx_s = max(0, int(round(float(iface.get("tx") or 0) / dt)))

        fluxos_in = obj.get("fluxos") or []
        for fl in fluxos_in:
            ip = str(fl.get("ip") or "")
            porta = int(fl.get("porta") or 0)
            if ip and not self.destinos.eh_loopback(ip) and not (ip in ("0.0.0.0", "::") and porta == 0):
                self.destinos.obter_host_e_org(ip, resolver_async=True)
        self.destinos.aguardar_rapido(0.10)

        apps_agg: dict[str, dict] = {}
        fluxos_agg: dict[tuple, dict] = {}

        for fl in fluxos_in:
            chave_app = str(fl.get("app") or "").strip()
            ip = str(fl.get("ip") or "").strip()
            try:
                proto_num = int(fl.get("proto") or 6)
                porta = int(fl.get("porta") or 0)
                b_rx = max(0, int(fl.get("rx") or 0))
                b_tx = max(0, int(fl.get("tx") or 0))
            except (ValueError, TypeError):
                continue

            if not ip or self.destinos.eh_loopback(ip):
                continue
            if b_rx == 0 and b_tx == 0:
                continue

            rx_rate = int(round(b_rx / dt))
            tx_rate = int(round(b_tx / dt))
            if rx_rate == 0 and tx_rate == 0:
                continue

            app_info = self.catalogo.descrever_app(chave_app)
            app_id = app_info["id"]
            if app_id not in apps_agg:
                apps_agg[app_id] = {
                    "id": app_id,
                    "nome": app_info["nome"],
                    "icone": app_info["icone"],
                    "rx": 0,
                    "tx": 0,
                }
            apps_agg[app_id]["rx"] += rx_rate
            apps_agg[app_id]["tx"] += tx_rate

            proto_str = "TCP" if proto_num == 6 else ("UDP" if proto_num == 17 else str(proto_num))
            if ip in ("0.0.0.0", "::") and porta == 0:
                host, org = ("Vários", "Vários")
                ip_mostrar = ip
                servico = f"{proto_str} 0"
                f_key = (app_id, "Vários", "0.0.0.0", 0, proto_str)
            else:
                host, org = self.destinos.obter_host_e_org(ip, resolver_async=True)
                ip_mostrar = ip
                servico = servico_por_porta(porta, proto_num)
                f_key = (app_id, org, ip_mostrar, porta, proto_str)

            if f_key not in fluxos_agg:
                fluxos_agg[f_key] = {
                    "app": app_info["nome"],
                    "app_id": app_id,
                    "org": org,
                    "host": host,
                    "ip": ip_mostrar,
                    "porta": porta,
                    "proto": proto_str,
                    "servico": servico,
                    "rx": 0,
                    "tx": 0,
                }
            fluxos_agg[f_key]["rx"] += rx_rate
            fluxos_agg[f_key]["tx"] += tx_rate

        soma_apps_rx = sum(a["rx"] for a in apps_agg.values())
        soma_apps_tx = sum(a["tx"] for a in apps_agg.values())

        outros_rx = max(0, iface_rx_s - soma_apps_rx)
        outros_tx = max(0, iface_tx_s - soma_apps_tx)
        if outros_rx > 0 or outros_tx > 0:
            info_outros = self.catalogo.descrever_app("outros")
            apps_agg["outros"] = {
                "id": "outros",
                "nome": info_outros["nome"],
                "icone": None,
                "rx": outros_rx,
                "tx": outros_tx,
            }

        total_rx = max(iface_rx_s, soma_apps_rx)
        total_tx = max(iface_tx_s, soma_apps_tx)

        lista_apps = sorted(apps_agg.values(), key=lambda x: x["rx"] + x["tx"], reverse=True)
        lista_fluxos = sorted(fluxos_agg.values(), key=lambda x: x["rx"] + x["tx"], reverse=True)

        return {
            "t": t_val,
            "dt": dt,
            "total": {"rx": total_rx, "tx": total_tx},
            "apps": lista_apps,
            "fluxos": lista_fluxos,
        }

    def _publicar_amostra(self, amostra: dict):
        with self.lock:
            self.historico.append(amostra)
            filas = list(self.clientes_sse)
        for q in filas:
            try:
                q.put_nowait(amostra)
            except Exception:
                pass

    def _loop_demo(self):
        while not self.encerrando:
            time.sleep(2.0)
            if self.encerrando:
                break
            bruto = self._gerar_bruto_demo(int(time.time()))
            amostra = self._processar_agora_obj(bruto)
            if amostra is not None:
                self._publicar_amostra(amostra)

    def _loop_agora(self):
        # 1ª leitura apenas registra ultimo_t para evitar amostra velha/pico falso no início
        try:
            with open(self.args.agora, "r", encoding="utf-8") as f:
                inicial = json.load(f)
                self.ultimo_t_agora = int(inicial.get("t") or 0)
        except Exception:
            self.ultimo_t_agora = None

        while not self.encerrando:
            time.sleep(2.0)
            if self.encerrando:
                break
            try:
                with open(self.args.agora, "r", encoding="utf-8") as f:
                    obj = json.load(f)
            except Exception:
                continue

            t_atual = int(obj.get("t") or 0)
            if t_atual <= 0 or t_atual == self.ultimo_t_agora:
                continue
            if self.ultimo_t_agora is None:
                self.ultimo_t_agora = t_atual
                continue

            dt_real = t_atual - self.ultimo_t_agora
            self.ultimo_t_agora = t_atual
            if dt_real < 1 or dt_real > 10:
                continue

            if not obj.get("dt") or float(obj.get("dt") or 0) <= 0:
                obj["dt"] = float(dt_real)

            amostra = self._processar_agora_obj(obj)
            if amostra is not None:
                self._publicar_amostra(amostra)

    def _abrir_db_ro(self) -> sqlite3.Connection | None:
        caminho = os.path.abspath(self.args.db)
        if not os.path.isfile(caminho):
            return None
        uri = f"file:{urllib.parse.quote(caminho)}?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=5.0)

    def obter_limites(self) -> dict:
        caminho = os.path.abspath(self.args.db)
        tam = 0
        if os.path.isfile(caminho):
            try:
                tam = os.path.getsize(caminho)
                for sufixo in ("-wal", "-shm"):
                    if os.path.isfile(caminho + sufixo):
                        tam += os.path.getsize(caminho + sufixo)
            except OSError:
                pass

        conn = self._abrir_db_ro()
        if not conn:
            agora = int(time.time())
            return {"primeiro": agora - 3600, "ultimo": agora, "tamanho_db": tam}

        try:
            cur = conn.cursor()
            mins = []
            maxs = []
            for tab in ("hora", "minuto", "iface_hora", "iface_minuto"):
                try:
                    cur.execute(f"SELECT MIN(ts), MAX(ts) FROM {tab}")
                    r = cur.fetchone()
                    if r and r[0] is not None:
                        mins.append(int(r[0]))
                    if r and r[1] is not None:
                        maxs.append(int(r[1]))
                except sqlite3.Error:
                    pass
            agora = int(time.time())
            primeiro = min(mins) if mins else agora - 3600
            ultimo = max(maxs) if maxs else agora
            return {"primeiro": primeiro, "ultimo": ultimo, "tamanho_db": tam}
        finally:
            conn.close()

    def consultar_periodo(self, de: int, ate: int) -> dict:
        if ate < de:
            de, ate = ate, de
        dur = max(60, ate - de)
        usar_minuto = dur <= 7200
        resolucao = "min" if usar_minuto else "hora"
        passo_base = 60 if usar_minuto else 3600
        passo = max(passo_base, int(math.ceil(dur / 380.0 / passo_base)) * passo_base)

        conn = self._abrir_db_ro()
        if not conn:
            return {
                "de": de,
                "ate": ate,
                "resolucao": resolucao,
                "passo": passo,
                "serie": [],
                "apps": [],
                "fluxos": [],
                "iface": {"rx": 0, "tx": 0},
            }

        try:
            cur = conn.cursor()
            rows_fluxos = []
            rows_iface = []

            if usar_minuto:
                cur.execute(
                    "SELECT ts, app, proto, porta, ip, rx, tx FROM minuto WHERE ts >= ? AND ts <= ?",
                    (de, ate),
                )
                rows_fluxos.extend(cur.fetchall())
                cur.execute(
                    "SELECT ts, rx, tx FROM iface_minuto WHERE ts >= ? AND ts <= ?",
                    (de, ate),
                )
                rows_iface.extend(cur.fetchall())

                # Caso o período ≤ 2h esteja numa faixa antiga já consolidada em hora
                if not rows_fluxos:
                    cur.execute(
                        "SELECT ts, app, proto, porta, ip, rx, tx FROM hora WHERE ts >= ? AND ts <= ?",
                        (de, ate),
                    )
                    rows_fluxos.extend(cur.fetchall())
                    cur.execute(
                        "SELECT ts, rx, tx FROM iface_hora WHERE ts >= ? AND ts <= ?",
                        (de, ate),
                    )
                    rows_iface.extend(cur.fetchall())
            else:
                cur.execute(
                    "SELECT ts, app, proto, porta, ip, rx, tx FROM hora WHERE ts >= ? AND ts <= ?",
                    (de, ate),
                )
                rows_fluxos.extend(cur.fetchall())
                cur.execute(
                    "SELECT ts, rx, tx FROM iface_hora WHERE ts >= ? AND ts <= ?",
                    (de, ate),
                )
                rows_iface.extend(cur.fetchall())

                cur.execute("SELECT MAX(ts) FROM hora")
                r_max = cur.fetchone()
                corte_min = (int(r_max[0]) + 3600) if (r_max and r_max[0] is not None) else de
                de_min = max(de, corte_min)
                if de_min <= ate:
                    cur.execute(
                        "SELECT (ts / 3600) * 3600 AS th, app, proto, porta, ip, SUM(rx), SUM(tx) "
                        "FROM minuto WHERE ts >= ? AND ts <= ? GROUP BY th, app, proto, porta, ip",
                        (de_min, ate),
                    )
                    rows_fluxos.extend(cur.fetchall())
                    cur.execute(
                        "SELECT (ts / 3600) * 3600 AS th, SUM(rx), SUM(tx) "
                        "FROM iface_minuto WHERE ts >= ? AND ts <= ? GROUP BY th",
                        (de_min, ate),
                    )
                    rows_iface.extend(cur.fetchall())
        finally:
            conn.close()

        if not self.args.demo:
            self.destinos._ler_cache_resolved()

        serie_buckets: dict[int, dict[str, dict[str, int]]] = collections.defaultdict(
            lambda: collections.defaultdict(lambda: {"rx": 0, "tx": 0})
        )
        iface_buckets: dict[int, dict[str, int]] = collections.defaultdict(lambda: {"rx": 0, "tx": 0})
        apps_tot: dict[str, dict] = {}
        fluxos_tot: dict[tuple, dict] = {}

        total_iface_rx = 0
        total_iface_tx = 0
        for ts_i, rx_i, tx_i in rows_iface:
            b_ts = (int(ts_i) // passo) * passo
            rxi = max(0, int(rx_i or 0))
            txi = max(0, int(tx_i or 0))
            iface_buckets[b_ts]["rx"] += rxi
            iface_buckets[b_ts]["tx"] += txi
            total_iface_rx += rxi
            total_iface_tx += txi

        for ts_f, chave_app, proto_num, porta, ip, rx_f, tx_f in rows_fluxos:
            rxf = max(0, int(rx_f or 0))
            txf = max(0, int(tx_f or 0))
            if rxf == 0 and txf == 0:
                continue
            ip_s = str(ip or "").strip()
            if not ip_s or self.destinos.eh_loopback(ip_s):
                continue

            b_ts = (int(ts_f) // passo) * passo
            app_info = self.catalogo.descrever_app(str(chave_app or ""))
            app_id = app_info["id"]

            serie_buckets[b_ts][app_id]["rx"] += rxf
            serie_buckets[b_ts][app_id]["tx"] += txf

            if app_id not in apps_tot:
                apps_tot[app_id] = {
                    "id": app_id,
                    "nome": app_info["nome"],
                    "icone": app_info["icone"],
                    "rx": 0,
                    "tx": 0,
                }
            apps_tot[app_id]["rx"] += rxf
            apps_tot[app_id]["tx"] += txf

            p_num = int(proto_num or 6)
            p_porta = int(porta or 0)
            proto_str = "TCP" if p_num == 6 else ("UDP" if p_num == 17 else str(p_num))

            if ip_s in ("0.0.0.0", "::") and p_porta == 0:
                host, org = ("Vários", "Vários")
                servico = f"{proto_str} 0"
                f_key = (app_id, "Vários", "0.0.0.0", 0, proto_str)
            else:
                host, org = self.destinos.obter_host_e_org(ip_s, resolver_async=False)
                servico = servico_por_porta(p_porta, p_num)
                f_key = (app_id, org, ip_s, p_porta, proto_str)

            if f_key not in fluxos_tot:
                fluxos_tot[f_key] = {
                    "app": app_info["nome"],
                    "app_id": app_id,
                    "org": org,
                    "host": host,
                    "ip": ip_s,
                    "porta": p_porta,
                    "proto": proto_str,
                    "servico": servico,
                    "rx": 0,
                    "tx": 0,
                }
            fluxos_tot[f_key]["rx"] += rxf
            fluxos_tot[f_key]["tx"] += txf

        todos_buckets = sorted(set(serie_buckets.keys()) | set(iface_buckets.keys()))
        serie_out = []
        outros_tot_rx = 0
        outros_tot_tx = 0

        for b_ts in todos_buckets:
            apps_b = dict(serie_buckets.get(b_ts, {}))
            s_rx = sum(v["rx"] for v in apps_b.values())
            s_tx = sum(v["tx"] for v in apps_b.values())
            if_b = iface_buckets.get(b_ts, {"rx": 0, "tx": 0})
            o_rx = max(0, if_b["rx"] - s_rx)
            o_tx = max(0, if_b["tx"] - s_tx)
            if o_rx > 0 or o_tx > 0:
                apps_b["outros"] = {"rx": o_rx, "tx": o_tx}
                outros_tot_rx += o_rx
                outros_tot_tx += o_tx
            serie_out.append({"t": b_ts, "apps": apps_b})

        soma_apps_rx = sum(a["rx"] for a in apps_tot.values())
        soma_apps_tx = sum(a["tx"] for a in apps_tot.values())
        if outros_tot_rx == 0 and outros_tot_tx == 0:
            outros_tot_rx = max(0, total_iface_rx - soma_apps_rx)
            outros_tot_tx = max(0, total_iface_tx - soma_apps_tx)

        if outros_tot_rx > 0 or outros_tot_tx > 0:
            info_outros = self.catalogo.descrever_app("outros")
            apps_tot["outros"] = {
                "id": "outros",
                "nome": info_outros["nome"],
                "icone": None,
                "rx": outros_tot_rx,
                "tx": outros_tot_tx,
            }

        iface_final_rx = max(total_iface_rx, soma_apps_rx + outros_tot_rx)
        iface_final_tx = max(total_iface_tx, soma_apps_tx + outros_tot_tx)

        lista_apps = sorted(apps_tot.values(), key=lambda x: x["rx"] + x["tx"], reverse=True)
        lista_fluxos = sorted(fluxos_tot.values(), key=lambda x: x["rx"] + x["tx"], reverse=True)[:250]

        return {
            "de": de,
            "ate": ate,
            "resolucao": resolucao,
            "passo": passo,
            "serie": serie_out,
            "apps": lista_apps,
            "fluxos": lista_fluxos,
            "iface": {"rx": iface_final_rx, "tx": iface_final_tx},
        }

    def ler_config(self) -> dict:
        cfg = dict(CONFIG_PADRAO)
        cfg["ignorar_interfaces"] = list(CONFIG_PADRAO["ignorar_interfaces"])
        try:
            with open(self.args.config, "r", encoding="utf-8") as f:
                lido = json.load(f)
            if isinstance(lido, dict):
                if isinstance(lido.get("dias_minuto"), int) and 1 <= lido["dias_minuto"] <= 90:
                    cfg["dias_minuto"] = lido["dias_minuto"]
                if isinstance(lido.get("dias_total"), int) and (lido["dias_total"] == 0 or 7 <= lido["dias_total"] <= 36500):
                    cfg["dias_total"] = lido["dias_total"]
                if isinstance(lido.get("ignorar_interfaces"), list):
                    cfg["ignorar_interfaces"] = [str(x) for x in lido["ignorar_interfaces"] if str(x).strip()]
                if lido.get("unidade") in ("bytes", "bits"):
                    cfg["unidade"] = lido["unidade"]
        except Exception:
            pass
        return cfg

    def salvar_config(self, dados: dict) -> tuple[bool, str | dict]:
        if not isinstance(dados, dict):
            return (False, "JSON inválido")

        dm = dados.get("dias_minuto")
        dt = dados.get("dias_total")
        un = dados.get("unidade")
        ig = dados.get("ignorar_interfaces", CONFIG_PADRAO["ignorar_interfaces"])

        if isinstance(dm, bool) or not isinstance(dm, int) or not (1 <= dm <= 90):
            return (False, "dias_minuto deve ser inteiro entre 1 e 90")
        if isinstance(dt, bool) or not isinstance(dt, int) or not (dt == 0 or 7 <= dt <= 36500):
            return (False, "dias_total deve ser 0 (para sempre) ou entre 7 e 36500")
        if dt and dt < dm:
            return (False, "dias_total deve ser maior ou igual a dias_minuto")
        if un not in ("bytes", "bits"):
            return (False, "unidade deve ser 'bytes' ou 'bits'")
        if not isinstance(ig, list) or not all(isinstance(x, str) and x.strip() for x in ig):
            return (False, "ignorar_interfaces deve ser uma lista de strings")

        novo = {
            "dias_minuto": dm,
            "dias_total": dt,
            "ignorar_interfaces": [x.strip() for x in ig],
            "unidade": un,
        }

        caminho = os.path.abspath(self.args.config)
        tmp = caminho + ".tmp"
        try:
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(novo, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, caminho)
        except OSError as e:
            return (False, f"Erro ao gravar config: {e}")

        try:
            subprocess.run(
                ["systemctl", "kill", "-s", "HUP", "banda-coletor.service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
        except Exception:
            pass

        return (True, novo)


def encontrar_index_html() -> pathlib.Path | None:
    candidatos = [
        pathlib.Path(__file__).resolve().parent.parent / "web" / "index.html",
        pathlib.Path("/usr/local/lib/banda-monitor/web/index.html"),
    ]
    for c in candidatos:
        if c.is_file():
            return c
    return None


class BandaHTTPHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def _enviar_json(self, status: int, obj: dict | list):
        corpo = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        monitor: MonitorBanda = self.server.monitor  # type: ignore
        monitor.garantir_coleta()

        parsed = urllib.parse.urlparse(self.path)
        rota = parsed.path

        if rota in ("/", "/index.html"):
            arq = encontrar_index_html()
            if not arq:
                self.send_error(404, "index.html nao encontrado")
                return
            conteudo = arq.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(conteudo)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(conteudo)
            return

        if rota == "/api/historico":
            with monitor.lock:
                dados = list(monitor.historico)
            self._enviar_json(200, dados)
            return

        if rota == "/api/limites":
            self._enviar_json(200, monitor.obter_limites())
            return

        if rota == "/api/config":
            self._enviar_json(200, monitor.ler_config())
            return

        if rota == "/api/periodo":
            qs = urllib.parse.parse_qs(parsed.query)
            agora = int(time.time())
            try:
                de = int(qs.get("de", [agora - 3600])[0])
                ate = int(qs.get("ate", [agora])[0])
            except (ValueError, TypeError):
                self._enviar_json(400, {"erro": "Parâmetros de/ate inválidos"})
                return
            self._enviar_json(200, monitor.consultar_periodo(de, ate))
            return

        if rota.startswith("/icone/"):
            icon_id = urllib.parse.unquote(rota[len("/icone/") :])
            caminho = monitor.catalogo.icon_paths.get(icon_id)
            if not caminho:
                caminho = monitor.catalogo.resolver_caminho_icone(icon_id)
            if not caminho or not os.path.isfile(caminho):
                self.send_error(404, "Icone nao encontrado")
                return
            ctype, _ = mimetypes.guess_type(caminho)
            if not ctype:
                ctype = "image/svg+xml" if caminho.endswith(".svg") else "image/png"
            dados_ico = pathlib.Path(caminho).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(dados_ico)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(dados_ico)
            return

        if rota == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            fila: queue.Queue = queue.Queue(maxsize=30)
            with monitor.lock:
                monitor.clientes_sse.append(fila)
                monitor.ultimo_sse_ativo = time.monotonic()

            try:
                while not monitor.encerrando:
                    try:
                        amostra = fila.get(timeout=0.25)
                        payload = "data: " + json.dumps(amostra, ensure_ascii=False, separators=(",", ":")) + "\n\n"
                        self.wfile.write(payload.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        try:
                            r, _, _ = select.select([self.connection], [], [], 0)
                            if r:
                                d = self.connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
                                if not d:
                                    break
                        except BlockingIOError:
                            pass
                        except Exception:
                            break
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with monitor.lock:
                    if fila in monitor.clientes_sse:
                        monitor.clientes_sse.remove(fila)
                    monitor.ultimo_sse_ativo = time.monotonic()
            return

        self.send_error(404, "Nao encontrado")

    def do_POST(self):
        monitor: MonitorBanda = self.server.monitor  # type: ignore
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/config":
            self.send_error(404, "Nao encontrado")
            return

        porta = monitor.args.porta
        alvo = f"127.0.0.1:{porta}"
        origin = (self.headers.get("Origin") or "").strip()
        host = (self.headers.get("Host") or "").strip()

        origin_ok = origin in (f"http://{alvo}", f"https://{alvo}", alvo)
        host_ok = host == alvo
        if origin and not origin_ok:
            self._enviar_json(403, {"erro": "Origin não autorizado"})
            return
        if not origin_ok and not host_ok:
            self._enviar_json(403, {"erro": "Host/Origin não autorizado"})
            return

        try:
            tam = int(self.headers.get("Content-Length") or 0)
            if tam <= 0 or tam > 65536:
                self._enviar_json(400, {"erro": "Tamanho de corpo inválido"})
                return
            corpo = self.rfile.read(tam)
            dados = json.loads(corpo.decode("utf-8"))
        except Exception:
            self._enviar_json(400, {"erro": "JSON inválido"})
            return

        ok, res = monitor.salvar_config(dados)
        if not ok:
            self._enviar_json(400, {"erro": res})
        else:
            self._enviar_json(200, res)  # type: ignore


class BandaHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description="Banda - Monitor de consumo de rede por app")
    parser.add_argument("--porta", type=int, default=7788, help="Porta HTTP (padrao: 7788)")
    parser.add_argument(
        "--agora",
        type=str,
        default="/run/banda-monitor/agora.json",
        help="Caminho para agora.json gerado pelo coletor",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="/var/lib/banda-monitor/historico.db",
        help="Caminho para o banco SQLite historico.db",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="/etc/banda-monitor/config.json",
        help="Caminho para config.json",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Modo demonstracao com dados ficticios",
    )
    args = parser.parse_args()

    monitor = MonitorBanda(args)

    httpd = BandaHTTPServer(("127.0.0.1", args.porta), BandaHTTPHandler, bind_and_activate=False)
    httpd.monitor = monitor  # type: ignore

    if os.environ.get("LISTEN_FDS"):
        httpd.socket = socket.socket(fileno=3)
    else:
        httpd.server_bind()
        httpd.server_activate()

    def encerrar_limpo(signum=None, frame=None):
        monitor.encerrando = True
        try:
            httpd.socket.close()
        except Exception:
            pass
        os._exit(0)

    signal.signal(signal.SIGTERM, encerrar_limpo)
    signal.signal(signal.SIGINT, encerrar_limpo)

    def vigia_inatividade():
        while not monitor.encerrando:
            time.sleep(0.5)
            with monitor.lock:
                n_clientes = len(monitor.clientes_sse)
                ocioso = time.monotonic() - monitor.ultimo_sse_ativo
            if n_clientes == 0 and ocioso >= 60.0:
                encerrar_limpo()

    t_vigia = threading.Thread(target=vigia_inatividade, daemon=True)
    t_vigia.start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        encerrar_limpo()


if __name__ == "__main__":
    main()
