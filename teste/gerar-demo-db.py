#!/usr/bin/env python3
"""Gera um banco SQLite historico.db fictício com 40 dias de histórico para testes e demonstração."""
import math
import os
import random
import sqlite3
import sys
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS minuto (
    ts INTEGER NOT NULL,
    app TEXT NOT NULL,
    proto INTEGER NOT NULL,
    porta INTEGER NOT NULL,
    ip TEXT NOT NULL,
    rx INTEGER NOT NULL,
    tx INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS minuto_ts ON minuto(ts);

CREATE TABLE IF NOT EXISTS hora (
    ts INTEGER NOT NULL,
    app TEXT NOT NULL,
    proto INTEGER NOT NULL,
    porta INTEGER NOT NULL,
    ip TEXT NOT NULL,
    rx INTEGER NOT NULL,
    tx INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS hora_ts ON hora(ts);

CREATE TABLE IF NOT EXISTS iface_minuto (
    ts INTEGER PRIMARY KEY,
    rx INTEGER NOT NULL,
    tx INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS iface_hora (
    ts INTEGER PRIMARY KEY,
    rx INTEGER NOT NULL,
    tx INTEGER NOT NULL
);
"""

# Fluxos fictícios: (app_key, proto, porta, ip, rx_base_por_min, tx_base_por_min, peso_freq)
FLUXOS_DEMO = [
    ("exe:google-chrome", 17, 443, "142.250.78.142", 4_200_000, 180_000, 0.95),
    ("exe:google-chrome", 6, 443, "104.16.132.229", 1_100_000, 95_000, 0.85),
    ("exe:google-chrome", 6, 443, "160.79.104.20", 380_000, 140_000, 0.65),
    ("steam:Counter-Strike 2", 17, 27025, "155.133.226.70", 1_650_000, 520_000, 0.55),
    ("steam:Counter-Strike 2", 6, 443, "155.133.226.71", 2_800_000, 60_000, 0.35),
    ("flatpak:com.discordapp.Discord", 17, 443, "162.159.135.232", 950_000, 410_000, 0.75),
    ("flatpak:com.discordapp.Discord", 17, 3478, "162.159.136.232", 180_000, 165_000, 0.60),
    ("exe:spotify", 6, 443, "35.186.224.25", 720_000, 28_000, 0.70),
    ("nuvem:gdrive", 6, 443, "172.217.28.14", 640_000, 1_850_000, 0.50),
    ("backup", 6, 443, "172.217.29.46", 45_000, 3_400_000, 0.25),
    ("flatpak:org.telegram.desktop", 6, 443, "149.154.167.99", 540_000, 120_000, 0.60),
    ("exe:firefox", 6, 443, "13.224.161.90", 680_000, 52_000, 0.45),
    ("exe:firefox", 6, 443, "140.82.112.4", 290_000, 85_000, 0.40),
    ("svc:systemd-resolved", 17, 53, "1.1.1.1", 42_000, 18_000, 0.98),
    ("svc:systemd-resolved", 6, 853, "1.0.0.1", 28_000, 12_000, 0.50),
    ("flatpak:com.bitwarden.desktop", 6, 443, "151.101.1.69", 36_000, 14_000, 0.30),
]


def fator_horario(ts: int) -> float:
    hora_local = ((ts - 3 * 3600) % 86400) / 3600.0
    if 1 <= hora_local < 7:
        return 0.12
    if 7 <= hora_local < 12:
        return 0.65 + 0.25 * math.sin((hora_local - 7) / 5 * math.pi)
    if 12 <= hora_local < 19:
        return 0.85 + 0.25 * math.sin((hora_local - 12) / 7 * math.pi)
    return 1.15


def gerar_banco(caminho: str):
    os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
    if os.path.exists(caminho):
        os.remove(caminho)

    rng = random.Random(42)
    agora = int(time.time())
    ts_fim_min = (agora // 60) * 60
    ts_inicio_30d = ts_fim_min - 30 * 86400
    ts_inicio_40d = (ts_inicio_30d // 3600) * 3600 - 10 * 86400

    conn = sqlite3.connect(caminho)
    conn.executescript("PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL;")
    conn.executescript(SCHEMA)

    rows_hora = []
    rows_iface_hora = []

    # 1. Dias -40 a -30 consolidados por hora
    ts_h = ts_inicio_40d
    fim_h = (ts_inicio_30d // 3600) * 3600
    while ts_h < fim_h:
        f_h = fator_horario(ts_h)
        soma_rx = 0
        soma_tx = 0
        for app, proto, porta, ip, rx_b, tx_b, freq in FLUXOS_DEMO:
            if rng.random() <= min(1.0, freq + 0.15):
                mult = 60.0 * f_h * rng.uniform(0.55, 1.45) * freq
                rx = max(120, int(rx_b * mult))
                tx = max(80, int(tx_b * mult))
                rows_hora.append((ts_h, app, proto, porta, ip, rx, tx))
                soma_rx += rx
                soma_tx += tx
        overhead_rx = int(soma_rx * rng.uniform(0.03, 0.06))
        overhead_tx = int(soma_tx * rng.uniform(0.03, 0.06))
        rows_iface_hora.append((ts_h, soma_rx + overhead_rx, soma_tx + overhead_tx))
        ts_h += 3600

    # 2. Últimos 30 dias em minuto (amostragem de 5 min nos primeiros 28 dias e 1 min nas últimas 48h)
    rows_min = []
    rows_iface_min = []
    corte_48h = ts_fim_min - 2 * 86400

    ts_m = ts_inicio_30d
    while ts_m <= ts_fim_min:
        passo = 60 if ts_m >= corte_48h else 300
        escala = passo / 60.0
        f_h = fator_horario(ts_m)
        soma_rx = 0
        soma_tx = 0
        for app, proto, porta, ip, rx_b, tx_b, freq in FLUXOS_DEMO:
            if rng.random() <= freq:
                mult = escala * f_h * rng.uniform(0.35, 1.75)
                rx = max(60, int(rx_b * mult))
                tx = max(40, int(tx_b * mult))
                rows_min.append((ts_m, app, proto, porta, ip, rx, tx))
                soma_rx += rx
                soma_tx += tx
        overhead_rx = int(soma_rx * rng.uniform(0.03, 0.07))
        overhead_tx = int(soma_tx * rng.uniform(0.03, 0.07))
        rows_iface_min.append((ts_m, soma_rx + overhead_rx, soma_tx + overhead_tx))
        ts_m += passo

    with conn:
        conn.executemany("INSERT INTO hora VALUES (?,?,?,?,?,?,?)", rows_hora)
        conn.executemany("INSERT INTO iface_hora VALUES (?,?,?)", rows_iface_hora)
        conn.executemany("INSERT INTO minuto VALUES (?,?,?,?,?,?,?)", rows_min)
        conn.executemany("INSERT INTO iface_minuto VALUES (?,?,?)", rows_iface_min)

    conn.execute("PRAGMA optimize;")
    conn.close()
    tam = os.path.getsize(caminho)
    print(f"Banco demo gerado em {caminho} ({len(rows_min)} linhas minuto, {len(rows_hora)} linhas hora, {tam} bytes)")


if __name__ == "__main__":
    destino = sys.argv[1] if len(sys.argv) > 1 else "/tmp/banda-demo.db"
    gerar_banco(destino)
