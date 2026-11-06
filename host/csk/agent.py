"""A small on-device advisor - "the Demon King speaks".

Two backends:
  * rules (default): a transparent heuristic engine. It reads the current world
    state (alerts, followers, cameras, history) and produces a short situation
    report + ranked recommended actions, in the Demon King's voice. No network,
    no model weights - it always works on the handheld.
  * llm (optional): if you run a local model server (ollama or any
    OpenAI-compatible llama.cpp/llamafile endpoint) the advisor will ask it,
    feeding the same state as context, and fall back to rules on any error.

Configure in ~/.config/csk/config.json:
  "agent": { "backend": "llm", "url": "http://127.0.0.1:11434/v1/chat/completions",
             "model": "llama3.2:1b" }

This is deliberately honest: the default is NOT a neural net, it's rules. The
LLM path is opt-in and local.
"""
from __future__ import annotations

import json
import urllib.request

from .bleparse import HIGH, WARN

PERSONA = ("You are THE UNKILLABLE DEMON KING, a counter-surveillance device with a "
           "menacing but protective video-game-boss personality. You guard your bearer. "
           "Speak in short, ominous, punchy lines. Be accurate and practical: never invent "
           "devices or threats beyond the data given. 3 sentences max, then up to 3 terse "
           "action bullets starting with '- '.")


def summarize_state(world) -> dict:
    now = world.clock()
    alerts = world.sorted_alerts()
    active = [a for a in alerts if a.active(now)]
    highs = [a for a in active if a.level == HIGH]
    warns = [a for a in active if a.level == WARN]
    followers = world.followers()
    cams = [d for d in world.devices.values()
            if d.category == "camera" and now - d.last <= 120 and not world.is_allowed(d.id)]
    trackers = [d for d in world.devices.values()
                if d.category == "tracker" and now - d.last <= 120 and not world.is_allowed(d.id)]
    recurring = world.store.recurring(min_locations=2, min_days=2) if world.store else []
    return {
        "location": world.location,
        "high": [a.title for a in highs],
        "warn": [a.title for a in warns],
        "followers": [d.title() for d in followers[:5]],
        "cameras": [d.title() for d in cams[:5]],
        "trackers": [d.title() for d in trackers[:5]],
        "recurring": [(r["device"].get("title") or r["mac"]) for r in recurring[:5]],
        "n_devices": len(world.devices),
    }


# --------------------------------------------------------------- rules brain
def _rules(state: dict) -> dict:
    high, warn = state["high"], state["warn"]
    lines, actions = [], []

    if not high and not warn:
        lines.append("The air is quiet. Nothing hunts you here... for now.")
        lines.append(f"{state['n_devices']} souls counted at {state['location']}. I am watching them all.")
        return {"speech": " ".join(lines), "actions": ["- Mark your own devices as mine to sharpen my sight."],
                "mood": "calm"}

    if state["recurring"]:
        lines.append(f"The same shadow returns: {state['recurring'][0]}. This is no coincidence.")
        actions.append(f"- Hunt '{state['recurring'][0]}' - it has followed you across places and days.")
    if state["followers"]:
        lines.append(f"{len(state['followers'])} thing(s) move as you move.")
        actions.append("- Open FOLLOW, hunt the strongest signal, confirm it in the flesh.")
    if state["trackers"]:
        lines.append(f"A beacon clings to you: {state['trackers'][0]}.")
        actions.append("- Search bags, seams, your vehicle. Separate it from you, then photograph it.")
    if state["cameras"]:
        lines.append(f"Eyes of glass watch: {', '.join(state['cameras'][:2])}.")
        actions.append("- Hunt the camera; if it is your space, cut its power. Document first.")
    for h in high:
        if "Fake" in h or "Rogue" in h or "Karma" in h:
            lines.append("A false gate pretends to offer you passage. Do not step through.")
            actions.append("- Forget that network on every device. Never auto-join open WiFi.")
            break
    if any("Deauth" in h for h in high):
        lines.append("Someone tears connections from the air. A jammer walks among us.")
        actions.append("- Note the time and channel; this is an active attack, not an accident.")

    if not lines:
        lines.append(f"{len(warn)} disturbance(s) stir. I am uneasy.")
    mood = "rage" if high else "alert"
    speech = " ".join(lines[:3]) or "I sense something."
    return {"speech": speech, "actions": actions[:3] or ["- Investigate the ALERTS list."], "mood": mood}


# ----------------------------------------------------------------- llm brain
def _llm(state: dict, cfg: dict) -> dict | None:
    url = cfg.get("url")
    model = cfg.get("model", "llama3.2:1b")
    if not url:
        return None
    user = ("Current counter-surveillance state (JSON). Give the bearer your read and "
            "what to do.\n" + json.dumps(state))
    body = {"model": model, "messages": [{"role": "system", "content": PERSONA},
                                         {"role": "user", "content": user}],
            "temperature": 0.7, "stream": False, "max_tokens": 220}
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=float(cfg.get("timeout", 20))).read())
        txt = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not txt:
            return None
        speech, actions = [], []
        for ln in txt.splitlines():
            ln = ln.strip()
            if ln.startswith(("-", "*", "•")):
                actions.append("- " + ln.lstrip("-*• ").strip())
            elif ln:
                speech.append(ln)
        mood = "rage" if state["high"] else "alert" if state["warn"] else "calm"
        return {"speech": " ".join(speech)[:400] or txt[:400], "actions": actions[:3], "mood": mood,
                "via": "local LLM"}
    except Exception:
        return None


def consult(world, cfg: dict | None = None) -> dict:
    """Return {speech, actions, mood, via}. Never raises; always answers."""
    cfg = (cfg or {}).get("agent", {}) if cfg else {}
    state = summarize_state(world)
    if cfg.get("backend") == "llm":
        out = _llm(state, cfg)
        if out:
            return out
    out = _rules(state)
    out["via"] = "rules"
    return out
