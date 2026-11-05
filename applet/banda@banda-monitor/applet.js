const Applet = imports.ui.applet;
const Util = imports.misc.util;
const GLib = imports.gi.GLib;
const ByteArray = imports.byteArray;

const IGNORAR_RE = /^(lo|docker|veth|virbr|br-)/;
const FIG_SPACE = "\u2007";

function formatarTaxa(bytesPorSeg, fixo) {
    const v = Math.max(0, Number(bytesPorSeg) || 0);
    let numStr;
    let unid;

    if (v < 1024) {
        numStr = String(Math.round(v));
        unid = "B/s";
    } else if (v < 1024 * 1024) {
        const kb = v / 1024;
        numStr = kb < 10 ? kb.toFixed(1).replace(".", ",") : String(Math.round(kb));
        unid = "KB/s";
    } else {
        const mb = v / (1024 * 1024);
        numStr = mb < 10 ? mb.toFixed(1).replace(".", ",") : String(Math.round(mb));
        unid = "MB/s";
    }

    if (fixo) {
        while (numStr.length < 4) {
            numStr = FIG_SPACE + numStr;
        }
        if (unid === "B/s") {
            unid = FIG_SPACE + "B/s";
        }
    }
    return numStr + " " + unid;
}

// Versão compacta para a barra: "11K", "1,2M", "973" (sem "/s")
function curto(bytesPorSeg) {
    const v = Math.max(0, Number(bytesPorSeg) || 0);
    if (v < 1000) return String(Math.round(v));
    if (v < 1024 * 1000) { const k = v / 1024; return (k < 10 ? k.toFixed(1).replace(".", ",") : String(Math.round(k))) + "K"; }
    const m = v / (1024 * 1024); return (m < 10 ? m.toFixed(1).replace(".", ",") : String(Math.round(m))) + "M";
}

function rotulo(rx, tx) { return "↓ " + curto(rx) + "\n↑ " + curto(tx); }

class BandaApplet extends Applet.TextIconApplet {
    constructor(metadata, orientation, panelHeight, instanceId) {
        super(orientation, panelHeight, instanceId);
        this.hide_applet_icon();
        this._applet_label.set_style("font-size: 7.5pt; line-height: 1.05; min-width: 3.6em; text-align: left; font-feature-settings: 'tnum';");
        this.set_applet_label(rotulo(0, 0));
        this.set_applet_tooltip("Banda — Monitor de rede");
        this._anterior = null;
        this._tempoAnterior = 0;
        this._atualizar();
        this._loop = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 2, () => {
            this._atualizar();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _lerInterfaces() {
        const [ok, raw] = GLib.file_get_contents("/proc/net/dev");
        if (!ok || !raw) return null;
        const texto = typeof raw === "string" ? raw : ByteArray.toString(raw);
        const linhas = texto.split("\n");
        const dados = {};

        for (let i = 2; i < linhas.length; i++) {
            const lin = linhas[i].trim();
            if (!lin) continue;
            const sep = lin.indexOf(":");
            if (sep < 0) continue;
            const iface = lin.slice(0, sep).trim();
            if (IGNORAR_RE.test(iface)) continue;
            const cols = lin.slice(sep + 1).trim().split(/\s+/);
            if (cols.length >= 9) {
                const rx = parseInt(cols[0], 10) || 0;
                const tx = parseInt(cols[8], 10) || 0;
                dados[iface] = { rx, tx };
            }
        }
        return dados;
    }

    _atualizar() {
        const agora = GLib.get_monotonic_time() / 1000000.0;
        const atual = this._lerInterfaces();
        if (!atual) return;

        if (!this._anterior) {
            this._anterior = atual;
            this._tempoAnterior = agora;
            return;
        }

        const dt = agora - this._tempoAnterior;
        if (dt < 1.0) {
            return;
        }
        let totRx = 0;
        let totTx = 0;
        const linhasTip = [];

        for (const iface of Object.keys(atual)) {
            const cur = atual[iface];
            const prev = this._anterior[iface];
            if (!prev) continue;
            const rxRate = Math.max(0, (cur.rx - prev.rx) / dt);
            const txRate = Math.max(0, (cur.tx - prev.tx) / dt);
            totRx += rxRate;
            totTx += txRate;
            linhasTip.push(iface + ": ↓ " + formatarTaxa(rxRate, false) + "  ↑ " + formatarTaxa(txRate, false));
        }

        this._anterior = atual;
        this._tempoAnterior = agora;

        this.set_applet_label(rotulo(totRx, totTx));
        this.set_applet_tooltip("Agora: ↓ " + formatarTaxa(totRx, false) + "  ↑ " + formatarTaxa(totTx, false) + (linhasTip.length ? "\n" + linhasTip.join("\n") : "") + "\nClique para ver por aplicativo");
    }

    on_applet_clicked() {
        Util.spawnCommandLineAsync("sh -c 'if command -v google-chrome >/dev/null 2>&1; then exec google-chrome --app=http://127.0.0.1:7788/ --window-size=1280,800; elif command -v chromium >/dev/null 2>&1; then exec chromium --app=http://127.0.0.1:7788/ --window-size=1280,800; elif command -v chromium-browser >/dev/null 2>&1; then exec chromium-browser --app=http://127.0.0.1:7788/ --window-size=1280,800; else exec xdg-open http://127.0.0.1:7788/; fi'");
    }

    on_applet_removed_from_panel() {
        if (this._loop) {
            GLib.source_remove(this._loop);
            this._loop = null;
        }
    }
}

function main(metadata, orientation, panelHeight, instanceId) {
    return new BandaApplet(metadata, orientation, panelHeight, instanceId);
}
