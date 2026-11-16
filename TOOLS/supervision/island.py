"""`GET /api/island`: short, versioned summary of the board, for an external app (a menu-bar widget, a phone...).

`/api/state` is the board's internal state (~100 KB, changes with it); this is a contract: the fields of
`schema` 1 change neither name nor meaning. Adding a field is allowed; removing or renaming = `schema` 2.
Never a token, a worktree path or screen text (a screen can show code or a secret).

  {schema: 1, project, now, url,
   managers: [{id, role (supervisor | manager | integrator), status, task, progress, waiting,
               question: {text, choices: [str], ids: [str], since, kind, generic} | null}]   (supervisor "S" first if registered),
   alerts: [{level, code, who, msg}], objective: {title, done, n, percent, eta} | null, waiting: int}

`ids` and `since` are used by POST /api/island/answer:
  header X-Island-Token (file run/<project>/island.token, next to server.port), body {id, choice, since}.
  Only a CHOICE of the current question: no free text, no key, no other command. `since` must be that of the
  question still displayed: a question already answered (board, terminal, wmux) or replaced is refused (409),
  that is the rule against double answers.
"""
import re

SCHEMA = 1
ROLES = {"integrator": "integrator"}  # brain role -> exposed role; any other role is "manager"


def _choices(choices):
    """(labels, ids) of the choices, whatever their shape (strings, or {id, label}); a string has no id."""
    labels, ids = [], []
    for c in choices or []:
        label = str(c.get("label") or c.get("id") or "") if isinstance(c, dict) else str(c)
        if label:
            labels.append(label[:80])
            ids.append(str(c.get("id")) if isinstance(c, dict) and c.get("id") is not None else "")
    return labels, ids


def check_answer(agents, mid, body):
    """Check an answer from the island -> (HTTP code, message); 200 = the answer can be sent."""
    ag = (agents or {}).get(mid)
    if not ag or not ag.get("waiting"):
        return 409, "no question waiting any more"
    if str(body.get("since")) != str(ag.get("since")):
        return 409, "question already answered or replaced"
    _, ids = _choices(ag.get("choices"))
    if not body.get("choice") or str(body["choice"]) not in [i for i in ids if i]:
        return 400, "unknown choice"
    return 200, ""


# Generic wmux labels (no content) and the server's fallbacks: the app then shows its own text.
GENERIC_RE = re.compile(r"^\s*(?:(?:claude|codex)\s+)?(?:needs your (?:permission|attention)|is waiting for your input|waiting for your input)\W*$"
                        r"|^\s*a prompt is waiting for your answer in its pane\W*$", re.I)
PERMISSION_RE = re.compile(r"needs your permission", re.I)


def _question(ag):
    """`kind` (permission | question) and `generic` (empty text or generic wmux label), same schema: the app
    replaces a generic text with its own, translated."""
    if not ag.get("waiting"):
        return None
    labels, ids = _choices(ag.get("choices"))
    text = str(ag.get("reason") or "")[:300]
    return {"text": text, "choices": labels, "ids": ids, "since": ag.get("since"),
            "kind": "permission" if PERMISSION_RE.search(text) else "question",
            "generic": not text.strip() or bool(GENERIC_RE.match(text))}


def build(state, alerts, objective, agents, url=""):
    """Summary of the state (shape of `/api/state`); `agents`: wmux state per manager (server.AGENTS).
    The supervisor comes first (id "S", role "supervisor", same schema) when a pane is registered for it:
    status = wmux state of its pane, task = its last note."""
    managers = []
    sv, ag_s = state.get("supervisor") or {}, (agents or {}).get("S") or {}
    if sv.get("surface") or ag_s:
        notes = state.get("supervisor_notes") or []
        last = notes[-1].get("text", "") if notes and isinstance(notes[-1], dict) else ""
        managers.append({"id": "S", "role": "supervisor", "status": str(ag_s.get("state") or ""), "task": str(last)[:120],
                         "progress": 0, "waiting": bool(ag_s.get("waiting")), "question": _question(ag_s)})
    for mid in sorted(state.get("managers") or {}):
        if mid == "S" and managers:  # the supervisor also registered as a manager (sup.py note S) appears only once
            continue
        m = state["managers"][mid]
        ag = (agents or {}).get(mid) or {}
        question = _question(ag)
        managers.append({"id": mid, "role": ROLES.get(m.get("role"), "manager"), "status": m.get("status") or "",
                         "task": m.get("task") or "", "progress": m.get("progress") or 0,
                         "waiting": bool(ag.get("waiting")), "question": question})
    obj = None
    if objective:
        obj = {k: objective.get(k) for k in ("title", "done", "n", "percent", "eta")}
    return {"schema": SCHEMA, "project": state.get("project") or "", "now": state.get("now") or 0, "url": url,
            "managers": managers,
            "alerts": [{k: a.get(k, "") for k in ("level", "code", "who", "msg")} for a in alerts or []],
            "objective": obj, "waiting": sum(1 for m in managers if m["waiting"])}
