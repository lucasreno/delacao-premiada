"""Transforma o fluxo de amostras em Blocos de Trabalho, Migalhas e Lacunas.

Regras (ver CONTEXT.md):
- chamada ativa vence ociosidade e vence a janela ativa em outro monitor;
- trabalho contíguo < MIGALHA_S é absorvido pelo bloco vizinho e dedatado;
- vazio (ocioso/sem dados) <= STITCH_S é emendado ao bloco anterior;
- vazio maior vira Lacuna (pergunta na Revisão).
"""

import re
from collections import Counter

from . import config

TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{1,6})\b")
BROWSERS = ("chrome", "chromium", "firefox", "edge", "msedge", "brave", "opera", "vivaldi")
JETBRAINS = ("idea", "intellij", "pycharm", "webstorm", "phpstorm", "goland",
             "rider", "clion", "datagrip", "jetbrains")
TERMINALS = ("gnome-terminal", "konsole", "alacritty", "kitty", "wezterm", "xterm",
             "tilix", "terminator", "windowsterminal", "wt", "cmd", "powershell", "mintty")


def context_key(app, title):
    a = (app or "").lower()
    t = title or ""
    m = TICKET_RE.search(t)
    if m:
        return f"ticket:{m.group(1)}"
    if any(x in a for x in JETBRAINS):
        seg = re.split(r"\s+[–—-]\s+", t)[0].strip()
        return f"dev:{seg or a}"
    if "code" in a:
        parts = [p.strip() for p in t.split(" - ") if p.strip()]
        if parts and parts[-1].lower().startswith("visual studio code"):
            parts = parts[:-1]
        return f"dev:{parts[-1] if parts else 'vscode'}"
    if any(b in a for b in BROWSERS):
        parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", t) if p.strip()]
        if parts and any(b in parts[-1].lower() for b in BROWSERS + ("google chrome",)):
            parts = parts[:-1]
        return f"web:{parts[-1] if parts else 'navegador'}"
    if any(x in a for x in TERMINALS):
        return "term:terminal"
    return f"app:{a or 'desconhecido'}"


def sample_key(s, idle_after_s):
    if s["in_call"]:
        return f"call:{s['call_title'] or 'chamada'}"
    if s["idle_ms"] >= idle_after_s * 1000:
        return "__vazio__"
    return context_key(s["app"], s["title"])


def build_spans(samples, poll_s=config.POLL_S, idle_after_s=config.IDLE_AFTER_S,
                no_data_gap_s=config.NO_DATA_GAP_S):
    spans = []
    prev_ts = None
    for s in samples:
        ts = s["ts"]
        if prev_ts is not None and ts - prev_ts > no_data_gap_s:
            spans.append({"key": "__vazio__", "start": prev_ts + poll_s, "end": ts,
                          "titles": Counter(), "shadow": Counter()})
        key = sample_key(s, idle_after_s)
        if spans and spans[-1]["key"] == key:
            spans[-1]["end"] = ts + poll_s
        else:
            spans.append({"key": key, "start": ts, "end": ts + poll_s,
                          "titles": Counter(), "shadow": Counter()})
        sp = spans[-1]
        sp["titles"][s["title"] or s["app"] or "?"] += poll_s
        if s["in_call"] and s["idle_ms"] < idle_after_s * 1000:
            sk = context_key(s["app"], s["title"])
            if not sk.startswith("call:"):
                sp["shadow"][sk] += poll_s
        prev_ts = ts
    return spans


def consolidate(spans, migalha_s=config.MIGALHA_S, stitch_s=config.STITCH_S):
    out, migalhas = [], []
    pending_start = None

    def top_title(sp):
        return sp["titles"].most_common(1)[0][0] if sp["titles"] else ""

    for sp in spans:
        dur = sp["end"] - sp["start"]
        prev = out[-1] if out else None
        if sp["key"] == "__vazio__":
            if dur > stitch_s:
                out.append({"kind": "lacuna", "key": "lacuna", "start": sp["start"],
                            "end": sp["end"], "titles": Counter(), "shadow": Counter()})
                pending_start = None
            elif prev and prev["kind"] == "work":
                prev["end"] = sp["end"]
            continue
        if dur < migalha_s:
            migalhas.append({"ts": sp["start"], "dur_s": dur, "key": sp["key"],
                             "title": top_title(sp)})
            if prev and prev["kind"] == "work":
                prev["end"] = sp["end"]
            elif pending_start is None:
                pending_start = sp["start"]
            continue
        start = pending_start if pending_start is not None else sp["start"]
        pending_start = None
        if prev and prev["kind"] == "work" and prev["key"] == sp["key"]:
            prev["end"] = sp["end"]
            prev["titles"] += sp["titles"]
            prev["shadow"] += sp["shadow"]
        else:
            out.append({"kind": "work", "key": sp["key"], "start": start, "end": sp["end"],
                        "titles": sp["titles"].copy(), "shadow": sp["shadow"].copy()})
    return out, migalhas


def fit_to_period(blocks, period_start, period_end):
    """Recorta os blocos ao período do ponto e emenda as costuras: o primeiro
    estica até a entrada, o último até a saída, e cada buraco interno gruda
    no bloco anterior (a pausa pertence ao trabalho que acabou de fechar)."""
    inside = []
    for b in blocks:
        if b["end"] <= period_start or b["start"] >= period_end:
            continue
        nb = dict(b)
        nb["start"] = max(b["start"], period_start)
        nb["end"] = min(b["end"], period_end)
        inside.append(nb)
    if not inside:
        return []
    inside[0]["start"] = period_start
    inside[-1]["end"] = period_end
    for i in range(len(inside) - 1):
        if inside[i]["end"] < inside[i + 1]["start"]:
            inside[i]["end"] = inside[i + 1]["start"]
    return inside


def segment(samples, periods):
    """Pipeline completo: amostras + períodos da Jornada -> (blocos, migalhas).

    `periods` é uma lista de (start_ts, end_ts); se vazia, usa o intervalo
    coberto pelas amostras (dia sem ponto informado).
    """
    if not samples:
        return [], []
    if not periods:
        periods = [(samples[0]["ts"], samples[-1]["ts"] + config.POLL_S)]
    all_blocks, all_migalhas = [], []
    for ps, pe in periods:
        chunk = [s for s in samples if ps <= s["ts"] < pe]
        if not chunk:
            continue
        spans = build_spans(chunk)
        blocks, migalhas = consolidate(spans)
        all_blocks += fit_to_period(blocks, ps, pe)
        all_migalhas += migalhas
    return all_blocks, all_migalhas
