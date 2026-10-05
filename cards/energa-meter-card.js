/* Karta Lovelace imitująca "Ostatnie odczyty licznika" z portalu ML.
 * Zasób (typ: module), np.: /local/energa-meter/energa-meter-card.js
 *
 * Konfiguracja:
 *   type: custom:energa-meter-card
 *   title: Ostatnie odczyty licznika     # opcjonalnie
 *   digits_before: 8                     # cyfry całkowite (domyślnie 8)
 *   decimals: 4                          # cyfry po przecinku (domyślnie 4)
 *   animate_on_load: false               # true = przewijanie od zera przy pierwszym wyświetleniu
 *   show_last_change: true               # "Ostatnia zmiana": kiedy zmieniła się wartość (last_changed)
 *   show_last_refresh: true              # "Ostatnie odświeżenie": ostatnie udane pobranie z portalu
 *   rows:
 *     - {label: "A+ strefa 1", entity: sensor.dom_pobor_strefa_1, color: green}
 *     - {label: "A- strefa 1", entity: sensor.dom_oddanie_strefa_1, color: grey}
 *
 * Gdy wartość sensora się zmieni, zmieniające się cyfry przewijają się jak w liczniku
 * mechanicznym (od prawej do lewej, z lekkim opóźnieniem).
 */
const EMC_VERSION = "0.3.0"; // 0.3.0: opcje show_last_change / show_last_refresh
console.info(`%c ENERGA-METER-CARD %c v${EMC_VERSION} `, "color:#fff;background:#0e6b5c;font-weight:bold", "color:#0e6b5c");
const EMC_COLORS = {
  green: "linear-gradient(#8cc63f,#4a8a0f)",
  grey: "linear-gradient(#cfcfcf,#9a9a9a)",
  red: "linear-gradient(#8a1f4f,#5a0f30)",
};
const EMC_H = 30; // wysokość cyfry w px
const EMC_MS = 900; // czas przewijania
const EMC_STAGGER = 70; // opóźnienie między kolejnymi cyframi (od prawej)

class EnergaMeterCard extends HTMLElement {
  setConfig(config) {
    if (!config || !Array.isArray(config.rows) || !config.rows.length) {
      throw new Error("Brak 'rows'");
    }
    this._config = {
      digits_before: 8,
      decimals: 4,
      animate_on_load: false,
      show_last_change: true,
      show_last_refresh: true,
      ...config,
    };
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    if (!this._built) this._build();
    this._rows.forEach((row) => this._update(row));
    this._updateInfo();
  }

  getCardSize() {
    return this._config ? this._config.rows.length + 1 : 4;
  }

  // ---------- budowa DOM (jednorazowo) ----------
  _build() {
    const c = this._config;
    if (!this._root) this._root = this.attachShadow({ mode: "open" });
    this._root.innerHTML = `
      <style>
        ha-card{padding:16px}
        h2{margin:0 0 12px;font-weight:400;font-size:20px;color:var(--secondary-text-color)}
        .row{display:flex;align-items:center;gap:12px;margin:8px 0;flex-wrap:wrap}
        .row.na .num{opacity:.45}
        .lbl{width:120px;font-size:14px;color:var(--primary-text-color)}
        .lbl small{display:block;font-size:11px;color:var(--secondary-text-color)}
        .num{display:flex;align-items:center;gap:2px}
        .d{position:relative;width:22px;height:${EMC_H}px;overflow:hidden;border-radius:3px;
           box-shadow:inset 0 -2px 0 rgba(0,0,0,.2),inset 0 2px 0 rgba(255,255,255,.15)}
        .strip{position:absolute;left:0;top:0;width:100%;
               transition:transform ${EMC_MS}ms cubic-bezier(.2,.8,.2,1)}
        .strip.snap{transition:none}
        .s{height:${EMC_H}px;line-height:${EMC_H}px;text-align:center;color:#fff;font-size:20px}
        .info{font-size:12px;color:var(--secondary-text-color);margin:-6px 0 10px;line-height:1.5}
        .info:empty{display:none}
        i{font-style:normal;margin:0 2px;color:var(--secondary-text-color)}
        @media (prefers-reduced-motion:reduce){.strip{transition:none}}
      </style>
      <ha-card>
        ${c.title === "" ? "" : `<h2>${this._esc(c.title ?? "Ostatnie odczyty licznika")}</h2>`}
        <div class="info" id="info"></div>
        <div id="rows"></div>
      </ha-card>`;
    const host = this._root.getElementById("rows");
    this._rows = c.rows.map((cfg) => this._buildRow(host, cfg));
    this._built = true;
  }

  _buildRow(host, cfg) {
    const c = this._config;
    const el = document.createElement("div");
    el.className = "row";
    el.innerHTML = `<div class="lbl">${this._esc(cfg.label ?? cfg.entity)}<small class="ts"></small></div>
                    <div class="num"></div>`;
    const num = el.querySelector(".num");
    const digits = [];
    const mk = (bg) => {
      const d = document.createElement("span");
      d.className = "d";
      d.style.background = bg;
      const strip = document.createElement("div");
      strip.className = "strip snap";
      strip.innerHTML = Array.from({ length: 20 }, (_, i) => `<div class="s">${i % 10}</div>`).join("");
      d.appendChild(strip);
      num.appendChild(d);
      const dg = { strip, cur: 0, wrapPending: false };
      strip.addEventListener("transitionend", () => this._snapIfWrapped(dg));
      digits.push(dg);
    };
    for (let i = 0; i < c.digits_before; i++) mk(EMC_COLORS[cfg.color || "green"] || EMC_COLORS.green);
    const comma = document.createElement("i");
    comma.textContent = ",";
    num.appendChild(comma);
    for (let i = 0; i < c.decimals; i++) mk(EMC_COLORS.red);
    host.appendChild(el);
    const row = { cfg, el, digits, ts: el.querySelector(".ts"), initialised: false, lastValue: undefined };
    digits.forEach((dg) => this._place(dg, 0, false));
    return row;
  }

  // ---------- informacje pod tytułem ----------
  _updateInfo() {
    const c = this._config;
    const info = this._root.getElementById("info");
    const states = c.rows.map((r) => this._hass.states[r.entity]).filter(Boolean);
    const latest = (vals) => vals.filter((v) => v && !isNaN(new Date(v))).sort((a, b) => new Date(b) - new Date(a))[0];
    const lines = [];
    if (c.show_last_change) {
      const t = latest(states.map((s) => s.last_changed));
      lines.push(`Ostatnia zmiana: ${t ? this._time(t) : "–"}`);
    }
    if (c.show_last_refresh) {
      // atrybut z integracji; gdy brak (starsza wersja) - last_updated encji
      const t = latest(states.map((s) => s.attributes?.last_refresh || s.last_updated));
      lines.push(`Ostatnie odświeżenie: ${t ? this._time(t) : "–"}`);
    }
    const html = lines.join("<br>");
    if (info.innerHTML !== html) info.innerHTML = html;
  }

  // ---------- aktualizacja ----------
  _update(row) {
    const st = this._hass.states[row.cfg.entity];
    const n = st ? Number(st.state) : NaN;
    const valid = st && st.state !== "unavailable" && st.state !== "unknown" && Number.isFinite(n);
    row.el.classList.toggle("na", !valid);
    row.ts.textContent = this._time(st?.attributes?.reading_time);
    if (!valid) return;
    if (row.lastValue === n) return;
    const c = this._config;
    const [ip, fp = ""] = n.toFixed(c.decimals).split(".");
    const text = ip.padStart(c.digits_before, "0").slice(-c.digits_before) + fp.padEnd(c.decimals, "0");
    const animate = row.initialised || c.animate_on_load;
    if (!row.initialised && c.animate_on_load) {
      // zacznij od zer i przewiń do wartości
      row.digits.forEach((dg) => this._place(dg, 0, false));
      requestAnimationFrame(() => requestAnimationFrame(() => this._apply(row, text, true)));
    } else {
      this._apply(row, text, animate);
    }
    row.initialised = true;
    row.lastValue = n;
  }

  _apply(row, text, animate) {
    const total = row.digits.length;
    row.digits.forEach((dg, i) => {
      const d = Number(text[i]);
      if (d === dg.cur && !dg.wrapPending) return;
      const fromRight = total - 1 - i;
      this._roll(dg, d, animate, animate ? fromRight * EMC_STAGGER : 0);
    });
  }

  // ---------- pojedyncza cyfra ----------
  _place(dg, pos, animate) {
    dg.strip.classList.toggle("snap", !animate);
    dg.strip.style.transform = `translateY(${-pos * EMC_H}px)`;
  }

  _snapIfWrapped(dg) {
    if (!dg.wrapPending) return;
    dg.wrapPending = false;
    dg.strip.style.transitionDelay = "0ms";
    this._place(dg, dg.cur, false); // 10+d -> d bez animacji (ten sam obraz cyfry)
    void dg.strip.offsetHeight; // wymuś reflow
  }

  _roll(dg, d, animate, delay) {
    this._snapIfWrapped(dg); // dokończ poprzednie zawinięcie
    if (!animate) {
      dg.cur = d;
      this._place(dg, d, false);
      return;
    }
    // d >= cur: jedziemy w dół kolumny; d < cur: przewijamy przez 9->0 (pozycja d+10)
    const pos = d >= dg.cur ? d : d + 10;
    dg.wrapPending = pos >= 10;
    dg.cur = d;
    dg.strip.style.transitionDelay = `${delay}ms`;
    this._place(dg, pos, true);
    if (dg.wrapPending) {
      // pewność dokończenia także gdy transitionend nie nadejdzie (np. karta ukryta)
      setTimeout(() => this._snapIfWrapped(dg), EMC_MS + delay + 80);
    }
  }

  // ---------- pomocnicze ----------
  _time(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return String(iso).replace("T", " ").slice(0, 16);
    const tz = this._hass?.config?.time_zone;
    const opts = {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    };
    try {
      return new Intl.DateTimeFormat("sv-SE", tz ? { ...opts, timeZone: tz } : opts).format(d);
    } catch (e) {
      return new Intl.DateTimeFormat("sv-SE", opts).format(d); // nieznana strefa -> strefa przeglądarki
    }
  }

  _esc(s) {
    return String(s).replace(/[&<>"]/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
  }
}

if (!customElements.get("energa-meter-card")) {
  customElements.define("energa-meter-card", EnergaMeterCard);
}
window.customCards = window.customCards || [];
window.customCards.push({
  type: "energa-meter-card",
  name: "Energa – odczyty licznika",
  description: "Licznik w stylu portalu Mój Licznik z animacją przewijania cyfr.",
});
