#!/usr/bin/env python3
"""
viewer.py - the local web viewer for the mail digest.

Serves a small site on 127.0.0.1 showing each important mail's heading, an
AI summary, and a button that opens the original message exactly as it was
sent. Nothing is exposed to the network and nothing is uploaded anywhere:
the server binds to the loopback interface only, and every byte it serves
comes from digest_store.json on this machine.

Run it with:  .\\.venv\\Scripts\\python.exe viewer.py
"""

import http.server
import json
import os
import re
import socket
import sys
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_FILE = os.path.join(SCRIPT_DIR, "digest_store.json")
FEEDBACK_FILE = os.path.join(SCRIPT_DIR, "feedback.json")

HOST = "127.0.0.1"  # loopback only - never 0.0.0.0, see module docstring
DEFAULT_PORT = 8765

CATEGORY_ORDER = ["Classes", "Fests", "Other"]
CATEGORY_EMOJI = {"Classes": "\U0001F4DA", "Fests": "\U0001F389", "Other": "\U0001F4CC"}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_store():
    try:
        with open(STORE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("mails"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"generated_at": None, "mails": []}


def load_feedback():
    try:
        with open(FEEDBACK_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("reports"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"reports": []}


def save_feedback(data):
    """Atomic write, so an interrupted save cannot truncate the file."""
    tmp = FEEDBACK_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, FEEDBACK_FILE)


def add_report(mail_id, undo=False):
    """Record (or withdraw) a "not important" report for one mail."""
    store = load_store()
    mail = next((m for m in store["mails"] if m.get("id") == mail_id), None)
    if mail is None:
        return False, "unknown mail id"

    data = load_feedback()
    reports = [r for r in data.get("reports", []) if r.get("id") != mail_id]

    if not undo:
        reports.append({
            "id": mail_id,
            "subject": mail.get("subject", ""),
            "sender_address": mail.get("from_address", ""),
            "sender": mail.get("from", ""),
            "was_category": mail.get("category", ""),
            "reported_at": datetime.now(timezone.utc).isoformat(),
        })

    data["reports"] = reports
    save_feedback(data)
    return True, len(reports)


def reported_ids():
    return {r.get("id") for r in load_feedback().get("reports", [])}


def mails_for_ui():
    """The store, minus the bodies, plus whether each has been reported.

    Bodies are deliberately left out of this payload: they are the bulk of the
    file and are only needed when a message is actually opened, which the
    /original endpoint handles one at a time.
    """
    store = load_store()
    reported = reported_ids()
    out = []
    for m in store["mails"]:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        out.append({
            "id": m["id"],
            "subject": m.get("subject") or "(no subject)",
            "from": m.get("from") or "",
            "from_address": m.get("from_address") or "",
            "date": m.get("date") or "",
            "received_at": m.get("received_at") or "",
            "category": m.get("category") or "Other",
            "summary": m.get("summary") or "",
            "snippet": m.get("snippet") or "",
            "institution": bool(m.get("institution")),
            "rescued": bool(m.get("rescued")),
            "rescue_reason": m.get("rescue_reason") or "",
            "reported": m["id"] in reported,
            "has_body": bool(m.get("body_html") or m.get("body_text")),
        })
    return {"generated_at": store.get("generated_at"), "mails": out}


# ---------------------------------------------------------------------------
# Rendering the original message
# ---------------------------------------------------------------------------

def original_document(mail, allow_images=False):
    """The message exactly as it arrived, wrapped for safe display.

    The body itself is passed through unchanged - that is the entire point of
    the Original button. Safety comes from how it is *displayed*, not from
    editing it: the page is served with a Content-Security-Policy that permits
    no scripts, no frames and no network access of any kind, and the viewer
    loads it inside a sandboxed iframe. Remote images stay blocked until the
    reader asks for them, because a remote image in an email is usually a
    tracking pixel that reports back when the mail was read.
    """
    body_html = mail.get("body_html") or ""
    if not body_html:
        text = mail.get("body_text") or mail.get("snippet") or "(no content)"
        body_html = "<pre class='plain'>" + escape_html(text) + "</pre>"

    img_src = "img-src data: https: http: cid:;" if allow_images else "img-src data:;"
    csp = (
        "default-src 'none'; "
        "style-src 'unsafe-inline'; "
        + img_src +
        " font-src data:; form-action 'none'; base-uri 'none'; frame-ancestors 'self';"
    )

    header_rows = []
    for label, value in (
        ("From", mail.get("from")),
        ("To", mail.get("to")),
        ("Cc", mail.get("cc")),
        ("Date", mail.get("date")),
        ("Subject", mail.get("subject")),
    ):
        if value:
            header_rows.append(
                "<tr><th>" + escape_html(label) + "</th><td>"
                + escape_html(value) + "</td></tr>"
            )

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta http-equiv='Content-Security-Policy' content=\"" + csp + "\">"
        "<style>"
        "body{margin:0;padding:20px;font:14px/1.6 system-ui,-apple-system,"
        "Segoe UI,sans-serif;color:#111;background:#fff;}"
        "table.hdr{border-collapse:collapse;margin-bottom:18px;width:100%;}"
        "table.hdr th{text-align:left;vertical-align:top;padding:3px 12px 3px 0;"
        "color:#666;font-weight:600;white-space:nowrap;width:1%;font-size:12px;"
        "text-transform:uppercase;letter-spacing:.04em;}"
        "table.hdr td{padding:3px 0;word-break:break-word;}"
        "hr{border:0;border-top:1px solid #e5e5e5;margin:0 0 18px;}"
        "pre.plain{white-space:pre-wrap;word-wrap:break-word;font:inherit;margin:0;}"
        "img{max-width:100%;height:auto;}"
        "</style></head><body>"
        "<table class='hdr'>" + "".join(header_rows) + "</table><hr>"
        + body_html +
        "</body></html>"
    )


def escape_html(text):
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mail Filter</title>
<style>
:root{
  --bg:#f6f7f9; --panel:#ffffff; --ink:#14161a; --muted:#6b7280;
  --line:#e6e8ec; --accent:#3b6df6; --accent-soft:#eaf0ff;
  --classes:#2f6df6; --fests:#c2410c; --other:#5b6472; --danger:#b42318;
  --shadow:0 1px 2px rgba(16,24,40,.05),0 8px 24px -12px rgba(16,24,40,.18);
  --radius:14px;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --bg:#0e1013; --panel:#16191e; --ink:#e8eaee; --muted:#98a1ae;
    --line:#262b33; --accent:#7aa2ff; --accent-soft:#1a2540;
    --classes:#7aa2ff; --fests:#fb923c; --other:#9aa4b2; --danger:#f97066;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 12px 32px -16px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"]{
  --bg:#0e1013; --panel:#16191e; --ink:#e8eaee; --muted:#98a1ae;
  --line:#262b33; --accent:#7aa2ff; --accent-soft:#1a2540;
  --classes:#7aa2ff; --fests:#fb923c; --other:#9aa4b2; --danger:#f97066;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 12px 32px -16px rgba(0,0,0,.7);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased;}
.wrap{max-width:860px;margin:0 auto;padding:28px 16px 80px;}

header{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:22px;}
h1{font-size:24px;letter-spacing:-.02em;margin:0 0 4px;}
.sub{color:var(--muted);font-size:13px;margin:0;}
.spacer{flex:1 1 auto}
.iconbtn{background:var(--panel);border:1px solid var(--line);color:var(--muted);
  width:36px;height:36px;border-radius:10px;cursor:pointer;font-size:15px;
  transition:transform .16s ease,color .16s ease,border-color .16s ease;}
.iconbtn:hover{transform:translateY(-1px);color:var(--ink);border-color:var(--accent);}
.iconbtn:active{transform:translateY(0) scale(.96);}

.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:18px;}
.search{flex:1 1 220px;min-width:180px;background:var(--panel);border:1px solid var(--line);
  color:var(--ink);border-radius:10px;padding:9px 12px;font:inherit;font-size:14px;
  transition:border-color .16s ease,box-shadow .16s ease;}
.search:focus{outline:none;border-color:var(--accent);
  box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 18%,transparent);}
.tab{background:var(--panel);border:1px solid var(--line);color:var(--muted);
  padding:8px 13px;border-radius:999px;cursor:pointer;font:inherit;font-size:13px;
  white-space:nowrap;transition:all .16s ease;}
.tab:hover{color:var(--ink);transform:translateY(-1px);}
.tab[aria-selected="true"]{background:var(--accent-soft);border-color:var(--accent);
  color:var(--accent);font-weight:600;}
.tab .n{opacity:.65;margin-left:5px;font-variant-numeric:tabular-nums;}

.sectitle{display:flex;align-items:center;gap:9px;margin:26px 0 12px;
  font-size:12px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;
  color:var(--muted);}
.sectitle::after{content:"";flex:1;height:1px;background:var(--line);}

.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  padding:15px 17px;margin-bottom:11px;box-shadow:var(--shadow);position:relative;
  animation:rise .42s cubic-bezier(.22,1,.36,1) backwards;
  transition:transform .18s ease,border-color .18s ease,opacity .28s ease;}
.card:hover{transform:translateY(-2px);border-color:color-mix(in srgb,var(--accent) 40%,var(--line));}
.card.leaving{opacity:0;transform:translateX(36px) scale(.97);}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){
  .card{animation:none}
  *{transition-duration:.01ms !important}
}

.subject{font-size:15.5px;font-weight:650;letter-spacing:-.01em;margin:0 0 5px;
  line-height:1.35;word-break:break-word;}
.meta{display:flex;gap:8px;flex-wrap:wrap;align-items:center;
  font-size:12.5px;color:var(--muted);margin-bottom:9px;}
.meta .who{font-weight:500;color:var(--ink);opacity:.8;}
.dot{opacity:.4}
.summary{font-size:14px;color:var(--ink);opacity:.9;margin:0 0 12px;word-break:break-word;}
.summary.empty{font-style:italic;opacity:.55;}

.badge{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:650;
  padding:2px 8px;border-radius:999px;letter-spacing:.02em;white-space:nowrap;}
.badge.cat{background:color-mix(in srgb,currentColor 12%,transparent);}
.cat-Classes{color:var(--classes)} .cat-Fests{color:var(--fests)} .cat-Other{color:var(--other)}
.badge.rescue{background:color-mix(in srgb,var(--accent) 14%,transparent);color:var(--accent);cursor:help;}

.actions{display:flex;gap:8px;flex-wrap:wrap;}
.btn{border:1px solid var(--line);background:transparent;color:var(--ink);
  padding:7px 14px;border-radius:9px;cursor:pointer;font:inherit;font-size:13px;
  font-weight:550;transition:all .16s ease;}
.btn:hover{transform:translateY(-1px);border-color:var(--accent);color:var(--accent);}
.btn:active{transform:translateY(0) scale(.97);}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff;}
.btn.primary:hover{color:#fff;filter:brightness(1.08);}
.btn.ghost{color:var(--muted);}
.btn.ghost:hover{color:var(--danger);border-color:var(--danger);}
.btn[disabled]{opacity:.5;cursor:not-allowed;transform:none;}

.empty-state{text-align:center;padding:64px 20px;color:var(--muted);}
.empty-state .big{font-size:34px;margin-bottom:10px;}

/* modal */
.overlay{position:fixed;inset:0;background:rgba(8,10,14,.55);backdrop-filter:blur(3px);
  display:flex;align-items:center;justify-content:center;padding:20px;z-index:50;
  opacity:0;pointer-events:none;transition:opacity .22s ease;}
.overlay.open{opacity:1;pointer-events:auto;}
.modal{background:var(--panel);border:1px solid var(--line);border-radius:16px;
  width:min(920px,100%);height:min(86vh,900px);display:flex;flex-direction:column;
  box-shadow:0 28px 70px -20px rgba(0,0,0,.5);overflow:hidden;
  transform:translateY(14px) scale(.985);opacity:0;
  transition:transform .26s cubic-bezier(.22,1,.36,1),opacity .2s ease;}
.overlay.open .modal{transform:none;opacity:1;}
.mhead{display:flex;align-items:center;gap:12px;padding:13px 16px;
  border-bottom:1px solid var(--line);}
.mtitle{font-weight:650;font-size:14.5px;flex:1;min-width:0;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;}
.mframe{flex:1;border:0;width:100%;background:#fff;}
.mfoot{padding:9px 16px;border-top:1px solid var(--line);font-size:12px;
  color:var(--muted);display:flex;align-items:center;gap:10px;flex-wrap:wrap;}

/* toast */
.toast{position:fixed;left:50%;bottom:26px;transform:translate(-50%,20px);
  background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:11px 15px;box-shadow:var(--shadow);display:flex;align-items:center;gap:12px;
  opacity:0;pointer-events:none;transition:all .24s cubic-bezier(.22,1,.36,1);z-index:60;
  font-size:13.5px;max-width:calc(100vw - 32px);}
.toast.show{opacity:1;transform:translate(-50%,0);pointer-events:auto;}
.toast button{background:none;border:0;color:var(--accent);font:inherit;font-weight:650;
  cursor:pointer;padding:0;}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>Mail Filter</h1>
      <p class="sub" id="sub">Loading…</p>
    </div>
    <div class="spacer"></div>
    <button class="iconbtn" id="theme" title="Toggle light / dark">◐</button>
    <button class="iconbtn" id="refresh" title="Reload from disk">⟳</button>
  </header>

  <div class="controls">
    <input class="search" id="q" type="search" placeholder="Search subject, sender or summary…" autocomplete="off">
    <button class="tab" data-f="all" aria-selected="true">All<span class="n"></span></button>
    <button class="tab" data-f="Classes" aria-selected="false">📚 Classes<span class="n"></span></button>
    <button class="tab" data-f="Fests" aria-selected="false">🎉 Fests<span class="n"></span></button>
    <button class="tab" data-f="Other" aria-selected="false">📌 Other<span class="n"></span></button>
    <button class="tab" data-f="filtered" aria-selected="false">🗃 Filtered<span class="n"></span></button>
  </div>

  <div id="list"></div>
</div>

<div class="overlay" id="overlay">
  <div class="modal" role="dialog" aria-modal="true" aria-label="Original message">
    <div class="mhead">
      <div class="mtitle" id="mtitle"></div>
      <button class="btn" id="imgs">Load images</button>
      <button class="btn" id="close">Close</button>
    </div>
    <iframe class="mframe" id="mframe" sandbox referrerpolicy="no-referrer"></iframe>
    <div class="mfoot" id="mfoot"></div>
  </div>
</div>

<div class="toast" id="toast"><span id="toastmsg"></span><button id="undo">Undo</button></div>

<script>
const $ = s => document.querySelector(s);
let MAILS = [], FILTER = "all", QUERY = "", CURRENT = null, LASTREPORT = null;

const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

function senderName(from){
  const m = String(from||"").match(/^\s*"?([^"<]*?)"?\s*<.*>\s*$/);
  const name = m ? m[1].trim() : "";
  return name || String(from||"").replace(/[<>]/g,"") || "unknown sender";
}

function when(iso, fallback){
  if(!iso) return fallback || "";
  const d = new Date(iso);
  if(isNaN(d)) return fallback || "";
  const mins = Math.round((Date.now() - d) / 60000);
  if(mins < 1)   return "just now";
  if(mins < 60)  return mins + "m ago";
  const hrs = Math.round(mins/60);
  if(hrs < 24)   return hrs + "h ago";
  const days = Math.round(hrs/24);
  if(days < 7)   return days + "d ago";
  return d.toLocaleDateString(undefined,{day:"numeric",month:"short"});
}

function visible(){
  const q = QUERY.trim().toLowerCase();
  return MAILS.filter(m => {
    const isFiltered = m.category === "Ignore" || m.reported;
    if(FILTER === "filtered"){ if(!isFiltered) return false; }
    else if(FILTER === "all"){ if(isFiltered) return false; }
    else { if(isFiltered || m.category !== FILTER) return false; }
    if(!q) return true;
    return (m.subject+" "+m.from+" "+m.summary+" "+m.snippet).toLowerCase().includes(q);
  });
}

function counts(){
  const c = {all:0, Classes:0, Fests:0, Other:0, filtered:0};
  for(const m of MAILS){
    if(m.category === "Ignore" || m.reported){ c.filtered++; continue; }
    c.all++;
    if(c[m.category] !== undefined) c[m.category]++;
  }
  return c;
}

function card(m, i){
  const el = document.createElement("article");
  el.className = "card";
  el.style.animationDelay = Math.min(i,12)*28 + "ms";
  el.dataset.id = m.id;
  const summary = m.summary
    ? `<p class="summary">${esc(m.summary)}</p>`
    : `<p class="summary empty">${esc(m.snippet || "No summary available — open the original.")}</p>`;
  const rescue = m.rescued
    ? `<span class="badge rescue" title="Kept visible because ${esc(m.rescue_reason)}">shielded</span>` : "";
  const reported = m.reported
    ? `<span class="badge rescue" title="You reported this as not important">reported</span>` : "";
  el.innerHTML = `
    <h2 class="subject">${esc(m.subject)}</h2>
    <div class="meta">
      <span class="badge cat cat-${esc(m.category)}">${esc(m.category)}</span>
      <span class="who">${esc(senderName(m.from))}</span>
      <span class="dot">·</span><span>${esc(when(m.received_at, m.date))}</span>
      ${rescue}${reported}
    </div>
    ${summary}
    <div class="actions">
      <button class="btn primary" data-act="open">Original</button>
      <button class="btn ghost" data-act="report">${m.reported ? "Not junk" : "Report"}</button>
    </div>`;
  el.querySelector('[data-act="open"]').onclick = () => openOriginal(m);
  el.querySelector('[data-act="report"]').onclick = e => report(m, el, e.currentTarget);
  return el;
}

function render(){
  const list = $("#list"); list.innerHTML = "";
  const rows = visible();
  const c = counts();
  document.querySelectorAll(".tab").forEach(t => {
    t.setAttribute("aria-selected", String(t.dataset.f === FILTER));
    t.querySelector(".n").textContent = c[t.dataset.f] ?? 0;
  });

  if(!rows.length){
    list.innerHTML = `<div class="empty-state"><div class="big">✦</div>
      <div>${QUERY ? "Nothing matches that search." :
        FILTER === "filtered" ? "Nothing has been filtered out." :
        "No mail here yet. Run mail_filter.py to fetch your digest."}</div></div>`;
    return;
  }

  if(FILTER === "all"){
    let i = 0;
    for(const cat of ["Classes","Fests","Other"]){
      const group = rows.filter(m => m.category === cat);
      if(!group.length) continue;
      const h = document.createElement("div");
      h.className = "sectitle";
      h.textContent = `${cat} · ${group.length}`;
      list.appendChild(h);
      for(const m of group) list.appendChild(card(m, i++));
    }
  } else {
    rows.forEach((m,i) => list.appendChild(card(m,i)));
  }
}

// --- original message ------------------------------------------------------
function openOriginal(m, withImages){
  CURRENT = m;
  $("#mtitle").textContent = m.subject;
  $("#mframe").src = `/original/${encodeURIComponent(m.id)}?images=${withImages?1:0}`;
  $("#imgs").textContent = withImages ? "Images shown" : "Load images";
  $("#imgs").disabled = !!withImages;
  $("#mfoot").textContent = withImages
    ? "Showing the message exactly as received, with remote images loaded."
    : "Shown exactly as received. Remote images are blocked — many are tracking pixels that tell the sender when you opened the mail.";
  $("#overlay").classList.add("open");
  document.body.style.overflow = "hidden";
}
function closeModal(){
  $("#overlay").classList.remove("open");
  document.body.style.overflow = "";
  setTimeout(() => { $("#mframe").src = "about:blank"; }, 240);
}
$("#close").onclick = closeModal;
$("#imgs").onclick = () => CURRENT && openOriginal(CURRENT, true);
$("#overlay").onclick = e => { if(e.target === $("#overlay")) closeModal(); };
document.addEventListener("keydown", e => { if(e.key === "Escape") closeModal(); });

// --- report ---------------------------------------------------------------
async function report(m, el, btn){
  const undo = !!m.reported;
  btn.disabled = true;
  try{
    const r = await fetch("/api/report", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({id:m.id, undo})
    });
    if(!r.ok) throw new Error(await r.text());
    m.reported = !undo;
    if(!undo){
      el.classList.add("leaving");
      setTimeout(render, 280);
      LASTREPORT = m;
      showToast("Marked not important. The classifier will learn from this.");
    } else {
      render();
      showToast("Report withdrawn.", false);
    }
  }catch(err){
    showToast("Could not save that: " + err.message, false);
    btn.disabled = false;
  }
}

let toastTimer = null;
function showToast(msg, undoable = true){
  $("#toastmsg").textContent = msg;
  $("#undo").style.display = undoable ? "" : "none";
  $("#toast").classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").classList.remove("show"), 6000);
}
$("#undo").onclick = async () => {
  if(!LASTREPORT) return;
  const m = LASTREPORT;
  await fetch("/api/report", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({id:m.id, undo:true})});
  m.reported = false; LASTREPORT = null;
  $("#toast").classList.remove("show");
  render();
};

// --- wiring ---------------------------------------------------------------
document.querySelectorAll(".tab").forEach(t => {
  t.onclick = () => { FILTER = t.dataset.f; render(); };
});
$("#q").oninput = e => { QUERY = e.target.value; render(); };
$("#refresh").onclick = load;
$("#theme").onclick = () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const next = cur === "dark" ? "light" : cur === "light" ? "dark"
    : (matchMedia("(prefers-color-scheme:dark)").matches ? "light" : "dark");
  document.documentElement.setAttribute("data-theme", next);
  try{ localStorage.setItem("mf-theme", next); }catch(e){}
};
try{
  const saved = localStorage.getItem("mf-theme");
  if(saved) document.documentElement.setAttribute("data-theme", saved);
}catch(e){}

async function load(){
  try{
    const r = await fetch("/api/mails");
    const data = await r.json();
    MAILS = data.mails || [];
    const shown = MAILS.filter(m => m.category !== "Ignore" && !m.reported).length;
    const gen = data.generated_at ? new Date(data.generated_at) : null;
    $("#sub").textContent =
      `${shown} mail${shown===1?"":"s"} worth your attention` +
      (gen ? ` · updated ${when(data.generated_at)}` : "");
    render();
  }catch(err){
    $("#sub").textContent = "Could not read the digest: " + err.message;
  }
}
load();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "MailFilterViewer/1.0"

    def log_message(self, fmt, *args):
        pass  # the console is for the digest, not an access log

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            return self._send(200, PAGE)

        if path == "/api/mails":
            return self._json(200, mails_for_ui())

        if path.startswith("/original/"):
            mail_id = urllib.parse.unquote(path[len("/original/"):])
            store = load_store()
            mail = next((m for m in store["mails"] if m.get("id") == mail_id), None)
            if mail is None:
                return self._send(404, "<p>That message is no longer in the store.</p>")
            params = urllib.parse.parse_qs(parsed.query)
            allow = params.get("images", ["0"])[0] == "1"
            doc = original_document(mail, allow_images=allow)
            csp = (
                "default-src 'none'; style-src 'unsafe-inline'; font-src data:; "
                + ("img-src data: https: http: cid:;" if allow else "img-src data:;")
                + " script-src 'none'; form-action 'none'; base-uri 'none';"
            )
            return self._send(200, doc, extra={"Content-Security-Policy": csp})

        return self._send(404, "<p>Not found.</p>")

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/api/report":
            return self._json(404, {"error": "not found"})

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 64_000:
            return self._json(400, {"error": "bad request body"})

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            mail_id = payload["id"]
        except (ValueError, KeyError, UnicodeDecodeError):
            return self._json(400, {"error": "expected JSON with an id"})

        ok, detail = add_report(mail_id, undo=bool(payload.get("undo")))
        if not ok:
            return self._json(404, {"error": detail})
        return self._json(200, {"ok": True, "reports": detail})


def free_port(preferred):
    """The preferred port, or the next free one after it."""
    for port in range(preferred, preferred + 20):
        with socket.socket() as sock:
            try:
                sock.bind((HOST, port))
                return port
            except OSError:
                continue
    return 0  # let the OS choose


def main():
    if not os.path.exists(STORE_FILE):
        print("No digest yet - run mail_filter.py first, then start this viewer.")
        return 1

    port = free_port(DEFAULT_PORT)
    httpd = http.server.ThreadingHTTPServer((HOST, port), Handler)
    url = "http://{}:{}/".format(HOST, httpd.server_address[1])

    count = len(load_store()["mails"])
    print("Mail Filter viewer running at {}".format(url))
    print("  {} message(s) in the store. Local only - nothing is exposed to the network.".format(count))
    print("  Press Ctrl+C to stop.")

    if "--no-browser" not in sys.argv:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
