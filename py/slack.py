"""Slack channel history for the Important Team Communications module (bot token, polling).
Needs SLACK_BOT_TOKEN (scopes channels:history or groups:history, plus users:read) and a channel the bot
was invited to. TVGUI_SLACK_FIXTURE=file.json ({"messages": [...], "users": {"U1": "Name"}}) replaces the
network for laptop work."""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://slack.com/api/"
SKIP = {"channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name", "channel_archive",
        "channel_unarchive", "group_join", "group_leave", "pinned_item", "unpinned_item"}
NAME_TTL = 3600
_names = {}  # user id -> (fetched_at, name)


def call(method, **params):
    """GET a Slack Web API method. Raises RuntimeError with Slack's error code on ok:false."""
    req = urllib.request.Request(
        API + method + "?" + urllib.parse.urlencode(params),
        headers={"Authorization": "Bearer " + os.environ["SLACK_BOT_TOKEN"]},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RuntimeError(f"rate limited, retry after {e.headers.get('Retry-After')}s") from e
        raise
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "slack error"))
    return data


def _fixture(method, **params):
    with open(os.environ["TVGUI_SLACK_FIXTURE"]) as f:
        data = json.load(f)
    if method == "conversations.history":
        return {"messages": data["messages"]}
    return {"user": {"real_name": data.get("users", {}).get(params.get("user"), "someone")}}


def user_name(uid, get=call, now=None):
    now = time.time() if now is None else now
    hit = _names.get(uid)
    if hit and now - hit[0] < NAME_TTL:
        return hit[1]
    try:
        u = get("users.info", user=uid)["user"]
        name = u.get("profile", {}).get("display_name") or u.get("real_name") or u.get("name") or "someone"
    except Exception:
        name = "someone"  # e.g. missing users:read; retried after NAME_TTL
    _names[uid] = (now, name)
    return name


def clean(text, name_of):
    """Slack mrkdwn -> plain text: mentions, channel refs, links and the &amp; &lt; &gt; escapes.
    shortcut: bold/italic/code markers and emoji shortcodes stay as typed."""

    def sub(m):
        body = m.group(1)
        head, _, label = body.partition("|")
        if head.startswith("@"):
            return "@" + name_of(head[1:])
        if head.startswith("#"):
            return "#" + (label or head[1:])
        if head.startswith("!"):
            return label or "@" + head[1:].split("^")[0]
        return label or re.sub(r"^\w+://", "", head).split("/")[0]

    return re.sub(r"<([^>]+)>", sub, text).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def messages(channel=None, limit=15, get=None):
    """Newest-first [{ts, author, text}] for display: no join/leave-style events, no thread replies."""
    get = get or (_fixture if os.environ.get("TVGUI_SLACK_FIXTURE") else call)
    channel = channel or os.environ.get("TVGUI_SLACK_CHANNEL")
    if not channel and get is call:
        raise RuntimeError("TVGUI_SLACK_CHANNEL not set")
    out = []
    for m in get("conversations.history", channel=channel, limit=limit)["messages"]:
        if m.get("subtype") in SKIP or (m.get("thread_ts") and m["thread_ts"] != m["ts"]):
            continue
        name_of = lambda u: user_name(u, get)
        author = name_of(m["user"]) if m.get("user") else m.get("username") or (m.get("bot_profile") or {}).get("name") or "bot"
        text = clean(m.get("text", ""), name_of).strip()
        if not text:
            text = "[file]" if m.get("files") else next((a.get("fallback") or a.get("text") or "" for a in m.get("attachments", [])), "") or "[message]"
        out.append({"ts": float(m["ts"]), "author": author, "text": text})
    return sorted(out, key=lambda x: -x["ts"])
