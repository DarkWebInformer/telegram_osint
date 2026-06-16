#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

_CORE_DEPS = [("telethon", "telethon>=1.43"), ("rich", "rich>=13.0")]
_OPTIONAL_DEPS = [("pyfiglet", "pyfiglet"), ("cryptg", "cryptg")]


def dwi_pip_install(packages: list[str]) -> bool:
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *packages]
        )
        return True
    except (subprocess.CalledProcessError, OSError):
        return False


def dwi_ensure_pip() -> bool:
    if importlib.util.find_spec("pip") is not None:
        return True
    print("pip not found in this Python; bootstrapping it with ensurepip...\n")
    try:
        subprocess.check_call([sys.executable, "-m", "ensurepip", "--upgrade"])
    except (subprocess.CalledProcessError, OSError):
        return False
    importlib.invalidate_caches()
    return importlib.util.find_spec("pip") is not None


def dwi_ensure_dependencies() -> None:
    missing = [pkg for mod, pkg in _CORE_DEPS if importlib.util.find_spec(mod) is None]
    optional_missing = [pkg for mod, pkg in _OPTIONAL_DEPS if importlib.util.find_spec(mod) is None]
    if not missing and not optional_missing:
        return
    if not dwi_ensure_pip():
        if missing:
            sys.exit(
                "\npip is not available in this Python and could not be bootstrapped.\n"
                f'Enable it:   "{sys.executable}" -m ensurepip --upgrade\n'
                f'Then run:    "{sys.executable}" -m pip install ' + " ".join(missing)
            )
        return
    if missing:
        print(f"Installing required packages into:\n  {sys.executable}\n")
        if not dwi_pip_install(missing):
            sys.exit(
                "\nAutomatic install failed. Install them yourself with:\n"
                f'  "{sys.executable}" -m pip install ' + " ".join(missing)
            )
        importlib.invalidate_caches()
    for mod, pkg in _OPTIONAL_DEPS:
        if importlib.util.find_spec(mod) is None:
            dwi_pip_install([pkg])
    importlib.invalidate_caches()
    still_missing = [mod for mod, _ in _CORE_DEPS if importlib.util.find_spec(mod) is None]
    if still_missing:
        sys.exit("Required packages still missing after install: " + ", ".join(still_missing))


try:
    dwi_ensure_dependencies()
except KeyboardInterrupt:
    sys.exit(130)

from telethon import TelegramClient
from telethon.tl import types
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.messages import (
    GetCommonChatsRequest,
    GetFullChatRequest,
    CheckChatInviteRequest,
)
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.errors import (
    FloodWaitError,
    ChannelPrivateError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)

from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.markup import escape
from rich import box
from rich.cells import cell_len

try:
    from pyfiglet import figlet_format
    _HAS_FIGLET = True
except ImportError:
    _HAS_FIGLET = False


CONFIG_FILE = Path("config.json")
EXPORT_DIR = Path("exports")
URL_RE = re.compile(r"https?://[^\s)>\]}'\"]+", re.IGNORECASE)
HASHTAG_RE = re.compile(r"(?<!\w)#(\w{2,})")
MENTION_RE = re.compile(r"(?<!\w)@(\w{3,})")
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TME_RE = re.compile(r"(?:https?://)?t\.me/(?:joinchat/|\+)?[A-Za-z0-9_+/-]+", re.IGNORECASE)
SOCIAL_DOMAINS = {
    "twitter.com": "twitter", "x.com": "twitter", "instagram.com": "instagram",
    "facebook.com": "facebook", "fb.com": "facebook", "youtube.com": "youtube",
    "youtu.be": "youtube", "tiktok.com": "tiktok", "github.com": "github",
    "gitlab.com": "gitlab", "reddit.com": "reddit", "discord.gg": "discord",
    "discord.com": "discord", "linkedin.com": "linkedin", "vk.com": "vk",
    "medium.com": "medium", "snapchat.com": "snapchat", "threads.net": "threads",
    "mastodon.social": "mastodon", "onlyfans.com": "onlyfans", "keybase.io": "keybase",
    "pastebin.com": "pastebin", "signal.me": "signal", "wa.me": "whatsapp",
}
WORD_RE = re.compile(r"[a-z']{3,}")
_STOPWORDS = {
    "the", "and", "you", "for", "are", "but", "not", "this", "that", "with", "have",
    "was", "what", "your", "from", "they", "his", "her", "she", "him", "all", "can",
    "will", "one", "out", "get", "has", "had", "who", "how", "why", "when", "where",
    "now", "just", "like", "see", "got", "too", "any", "our", "their", "them", "its",
    "him", "she", "may", "use", "way", "day", "new", "via", "yes", "off", "let", "été",
    "про", "это", "как", "что", "для", "при", "над", "под", "без", "над", "там",
}
_ID_ANCHORS = [
    (1_000_000, "2013-08-15"), (10_000_000, "2014-06-15"), (50_000_000, "2015-05-15"),
    (100_000_000, "2016-03-15"), (200_000_000, "2017-09-15"), (300_000_000, "2018-04-15"),
    (400_000_000, "2019-01-15"), (500_000_000, "2020-02-15"), (700_000_000, "2020-08-15"),
    (1_000_000_000, "2021-05-15"), (1_300_000_000, "2021-12-15"), (1_700_000_000, "2022-08-15"),
    (2_000_000_000, "2023-02-15"), (5_000_000_000, "2024-01-15"), (6_000_000_000, "2024-08-15"),
    (7_000_000_000, "2025-03-15"),
]
console = Console()


def dwi_print_banner() -> None:
    art = None
    if _HAS_FIGLET:
        top = figlet_format("Telegram", font="slant").rstrip("\n")
        bottom = figlet_format("OSINT", font="slant").rstrip("\n")
        widest = max((len(l) for l in (top + "\n" + bottom).splitlines()), default=0)
        if widest <= console.width:
            art = (top, bottom)
    if art:
        banner = Text()
        banner.append(art[0] + "\n", style="bold cyan")
        banner.append(art[1], style="bold magenta")
        console.print(banner)
    else:
        console.print("[bold cyan]Telegram[/] [bold magenta]OSINT[/]")
    console.print(
        Panel.fit(
            "[bold]Telegram OSINT[/] - Dark Web Informer edition\n"
            "[dim]Only reads what your authenticated account can already see.[/]",
            border_style="green",
        )
    )


def dwi_human(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def dwi_oneline(value: Any) -> str:
    return " ".join(str(value or "").split())


_DISPLAY_STRIP = {"So", "Sk", "Mn", "Me", "Cf", "Cc", "Cn", "Cs", "Co", "Zl", "Zp"}
_WIDTH_FILLERS = {"\u115f", "\u1160", "\u3164", "\uffa0"}


def dwi_display_safe(value: Any) -> str:
    s = unicodedata.normalize("NFC", dwi_human(value))
    out = []
    for ch in s:
        if ch in _WIDTH_FILLERS:
            continue
        if unicodedata.category(ch) in _DISPLAY_STRIP:
            continue
        if ch != " " and cell_len(ch) == 0:
            continue
        out.append(ch)
    cleaned = " ".join("".join(out).split())
    return cleaned or "-"


def dwi_parse_int(raw: str, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def dwi_parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Invalid date '{value}' (use YYYY-MM-DD or 'YYYY-MM-DD HH:MM')")


def dwi_safe_filename(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", text).strip("_") or "target"


def dwi_parse_target(raw: str) -> tuple[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return "entity", raw
    m = re.match(r"(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/(.+)$", raw, re.IGNORECASE)
    if m:
        path = m.group(1).strip("/")
        inv = re.match(r"\+([\w-]+)$", path) or re.match(r"joinchat/([\w-]+)$", path, re.IGNORECASE)
        if inv:
            return "invite", inv.group(1)
        cm = re.match(r"c/(\d+)", path)
        if cm:
            return "peer_channel", int(cm.group(1))
        username = path.split("/")[0].split("?")[0]
        return "entity", username
    if raw.startswith("@"):
        return "entity", raw[1:]
    if re.fullmatch(r"-?\d+", raw):
        return "entity", int(raw)
    if re.fullmatch(r"\+[\w-]+", raw):
        return "invite", raw[1:]
    return "entity", raw


def dwi_status_label(status: Any) -> Optional[str]:
    if status is None:
        return None
    if isinstance(status, types.UserStatusOffline) and getattr(status, "was_online", None):
        return f"offline (last seen {status.was_online.date().isoformat()})"
    return type(status).__name__.replace("UserStatus", "").lower() or None


def dwi_participant_role(user: Any) -> tuple[str, Optional[str]]:
    p = getattr(user, "participant", None)
    if isinstance(p, (types.ChannelParticipantCreator, types.ChatParticipantCreator)):
        return "creator", getattr(p, "rank", None)
    if isinstance(p, (types.ChannelParticipantAdmin, types.ChatParticipantAdmin)):
        return "admin", getattr(p, "rank", None)
    return "member", None


def dwi_extract_pivots(*texts: Optional[str]) -> dict[str, Any]:
    mentions: set[str] = set()
    tme: set[str] = set()
    socials: set[tuple[str, str]] = set()
    for t in texts:
        t = t or ""
        for name in MENTION_RE.findall(t):
            mentions.add("@" + name)
        for link in TME_RE.findall(t):
            tme.add(link if link.lower().startswith("http") else "https://" + link)
        for url in URL_RE.findall(t):
            host = urlparse(url if "://" in url else "http://" + url).netloc.lower().removeprefix("www.")
            platform = SOCIAL_DOMAINS.get(host)
            if platform:
                socials.add((platform, url))
    return {
        "mentions": sorted(mentions, key=str.lower),
        "tme_links": sorted(tme, key=str.lower),
        "socials": sorted(socials),
    }


def dwi_render_pivots(pivots: Optional[dict[str, Any]]) -> Optional[Panel]:
    if not pivots:
        return None
    lines: list[str] = []
    pc = pivots.get("personal_channel")
    if pc:
        handle = f"@{pc['username']}" if pc.get("username") else f"ID {pc.get('id')}"
        lines.append(f"[bold]Personal channel:[/] {escape(dwi_display_safe(pc.get('title')))} ({handle})")
    linked = pivots.get("linked_chat")
    if linked:
        handle = f"@{linked['username']}" if linked.get("username") else f"ID {linked.get('id')}"
        lines.append(f"[bold]Linked discussion group:[/] {escape(dwi_display_safe(linked.get('title')))} ({handle})")
    if pivots.get("mentions"):
        lines.append("[bold]Mentions:[/] " + escape(", ".join(pivots["mentions"][:30])))
    if pivots.get("tme_links"):
        lines.append("[bold]Telegram links:[/] " + escape(", ".join(pivots["tme_links"][:20])))
    if pivots.get("socials"):
        lines.append("[bold]External:[/] " + escape(", ".join(f"{p}: {u}" for p, u in pivots["socials"][:20])))
    if not lines:
        return None
    return Panel("\n".join(lines), title="[magenta]Pivots / next targets[/]", border_style="magenta")


@dataclass
class Config:
    api_id: int
    api_hash: str
    session_name: str

    def save(self) -> None:
        CONFIG_FILE.write_text(
            json.dumps(
                {
                    "api_id": self.api_id,
                    "api_hash": self.api_hash,
                    "session_name": self.session_name,
                },
                indent=4,
            )
        )
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except OSError:
            pass
        console.print(f"[green]Configuration saved to {CONFIG_FILE} (chmod 600).[/]")


def dwi_prompt_api_id() -> int:
    while True:
        raw = console.input("[cyan]API ID: [/]").strip()
        try:
            return int(raw)
        except ValueError:
            console.print("[red]API ID must be a number.[/]")


def dwi_resolve_config(args: argparse.Namespace) -> Config:
    file_cfg: dict[str, Any] = {}
    if CONFIG_FILE.exists():
        try:
            file_cfg = json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            console.print(f"[yellow]Could not read {CONFIG_FILE}: {exc}[/]")

    api_id = args.api_id or os.getenv("TG_API_ID") or file_cfg.get("api_id")
    api_hash = args.api_hash or os.getenv("TG_API_HASH") or file_cfg.get("api_hash")
    session = (
        args.session
        or os.getenv("TG_SESSION")
        or file_cfg.get("session_name")
    )

    if api_id and api_hash and session:
        try:
            api_id_int = int(api_id)
        except (TypeError, ValueError):
            console.print(f"[red]Invalid API ID {api_id!r}: must be numeric.[/]")
            sys.exit(1)
        if file_cfg:
            console.print("[dim]Using saved/provided credentials.[/]")
        return Config(api_id_int, str(api_hash), str(session))

    console.print("[yellow]Telegram API credentials needed.[/] "
                  "Create an app at https://my.telegram.org/auth")
    api_id_int = dwi_prompt_api_id()
    api_hash = console.input("[cyan]API Hash: [/]").strip()
    session = console.input("[cyan]Session name [tg]: [/]").strip() or "tg"
    cfg = Config(api_id_int, api_hash, session)
    if console.input("[green]Save these to config.json? (Y/n): [/]").strip().lower() in ("", "y", "yes"):
        cfg.save()
    return cfg


def dwi_chat_type(entity: Any) -> str:
    if isinstance(entity, types.User):
        return "User"
    if isinstance(entity, types.Chat):
        return "Group"
    if isinstance(entity, types.Channel):
        return "Megagroup" if getattr(entity, "megagroup", False) else "Channel"
    return type(entity).__name__


def dwi_media_label(msg: Any) -> Optional[str]:
    if msg.photo:
        return "photo"
    if msg.video:
        return "video"
    if msg.voice:
        return "voice"
    if msg.audio:
        return "audio"
    if msg.video_note:
        return "video_note"
    if msg.gif:
        return "gif"
    if msg.sticker:
        return "sticker"
    if msg.document:
        return "document"
    if msg.web_preview:
        return "webpage"
    if msg.media:
        return type(msg.media).__name__
    return None


def dwi_forward_source(msg: Any) -> tuple[Optional[str], Optional[int]]:
    fwd = getattr(msg, "forward", None)
    if fwd is None:
        raw = getattr(msg, "fwd_from", None)
        name = getattr(raw, "from_name", None) if raw is not None else None
        return name, None
    name: Optional[str] = None
    fid: Optional[int] = None
    chat = getattr(fwd, "chat", None)
    if chat is not None:
        name = getattr(chat, "title", None) or getattr(chat, "username", None)
        fid = getattr(chat, "id", None)
    if name is None:
        sender = getattr(fwd, "sender", None)
        if sender is not None:
            name = getattr(sender, "username", None) or getattr(sender, "first_name", None)
            fid = getattr(sender, "id", None)
    if name is None:
        name = getattr(fwd, "from_name", None)
    if fid is None:
        fid = getattr(fwd, "sender_id", None) or getattr(fwd, "chat_id", None)
    return name, fid


def dwi_format_birthday(b: Any) -> Optional[str]:
    if b is None:
        return None
    day = getattr(b, "day", None)
    month = getattr(b, "month", None)
    year = getattr(b, "year", None)
    if not day or not month:
        return None
    base = f"{day:02d}.{month:02d}"
    return f"{base}.{year}" if year else base


def dwi_estimate_account_age(user_id: Any) -> Optional[str]:
    if not isinstance(user_id, int) or user_id <= 0:
        return None
    ids = [a for a, _ in _ID_ANCHORS]
    if user_id <= ids[0]:
        return f"~{_ID_ANCHORS[0][1]} or earlier (rough)"
    if user_id >= ids[-1]:
        return f"~{_ID_ANCHORS[-1][1]} or later (rough)"
    for (id0, d0), (id1, d1) in zip(_ID_ANCHORS, _ID_ANCHORS[1:]):
        if id0 <= user_id <= id1:
            t0 = datetime.fromisoformat(d0)
            t1 = datetime.fromisoformat(d1)
            frac = (user_id - id0) / (id1 - id0)
            approx = t0 + (t1 - t0) * frac
            return f"~{approx.date().isoformat()} (interpolated, rough)"
    return None


def dwi_infer_timezone(by_hour: dict[int, int], min_samples: int = 30) -> Optional[dict[str, Any]]:
    counts = [int(by_hour.get(h, 0)) for h in range(24)]
    total = sum(counts)
    if total < min_samples:
        return None
    window = 6
    sums = [sum(counts[(s + i) % 24] for i in range(window)) for s in range(24)]
    trough_start = min(range(24), key=lambda s: sums[s])
    sleep_center_utc = (trough_start + (window - 1) / 2) % 24
    offset = round(3.5 - sleep_center_utc)
    while offset > 14:
        offset -= 24
    while offset < -12:
        offset += 24
    avg = total / 24
    trough_avg = sums[trough_start] / window
    depth = 1 - (trough_avg / avg) if avg else 0
    if total >= 200 and depth > 0.6:
        confidence = "high"
    elif total >= 60 and depth > 0.35:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "offset": offset,
        "label": f"UTC{offset:+d}",
        "confidence": confidence,
        "samples": total,
        "quiet_hours_utc": f"{trough_start:02d}:00-{(trough_start + window) % 24:02d}:00",
    }


def dwi_extract_user(user: Any, full: Any = None) -> dict[str, Any]:
    usernames = []
    if getattr(user, "usernames", None):
        usernames = [u.username for u in user.usernames if getattr(u, "username", None)]
    status = getattr(user, "status", None)
    status_name = type(status).__name__ if status else None
    last_online = None
    if isinstance(status, types.UserStatusOffline) and status.was_online:
        last_online = status.was_online.isoformat()
    photo = getattr(user, "photo", None)
    has_photo = photo is not None and not isinstance(photo, types.UserProfilePhotoEmpty)

    bio = getattr(full, "about", None) if full else None
    birthday = dwi_format_birthday(getattr(full, "birthday", None)) if full else None
    personal_channel_id = getattr(full, "personal_channel_id", None) if full else None
    has_stories = bool(getattr(full, "stories_pinned_available", False)) if full else None

    business_address = business_geo = business_timezone = business_open_now = None
    if full:
        loc = getattr(full, "business_location", None)
        if loc is not None:
            business_address = getattr(loc, "address", None)
            gp = getattr(loc, "geo_point", None)
            if gp is not None and getattr(gp, "lat", None) is not None:
                business_geo = f"{gp.lat}, {gp.long}"
        wh = getattr(full, "business_work_hours", None)
        if wh is not None:
            business_timezone = getattr(wh, "timezone_id", None)
            business_open_now = getattr(wh, "open_now", None)

    bot_description = bot_privacy_url = None
    bot_commands: list[str] = []
    if full:
        bi = getattr(full, "bot_info", None)
        if bi is not None:
            bot_description = getattr(bi, "description", None)
            bot_privacy_url = getattr(bi, "privacy_policy_url", None)
            for c in getattr(bi, "commands", None) or []:
                bot_commands.append(f"/{c.command} - {c.description}")

    return {
        "id": user.id,
        "username": user.username,
        "all_usernames": usernames or ([user.username] if user.username else []),
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": " ".join(p for p in (user.first_name, user.last_name) if p) or None,
        "phone_number": getattr(user, "phone", None),
        "bio": bio,
        "birthday": birthday,
        "account_created_estimate": dwi_estimate_account_age(getattr(user, "id", None)),
        "status": status_name,
        "last_online": last_online,
        "is_bot": getattr(user, "bot", None),
        "is_verified": getattr(user, "verified", None),
        "is_premium": getattr(user, "premium", None),
        "is_scam": getattr(user, "scam", None),
        "is_fake": getattr(user, "fake", None),
        "is_restricted": getattr(user, "restricted", None),
        "is_support": getattr(user, "support", None),
        "language_code": getattr(user, "lang_code", None),
        "dc_id": getattr(photo, "dc_id", None),
        "has_photo": has_photo,
        "has_stories": has_stories,
        "personal_channel_id": personal_channel_id,
        "business_address": business_address,
        "business_geo": business_geo,
        "business_timezone": business_timezone,
        "business_open_now": business_open_now,
        "bot_description": bot_description,
        "bot_commands": bot_commands,
        "bot_privacy_url": bot_privacy_url,
    }


def dwi_render_user_panel(data: dict[str, Any], title: str, style: str) -> Panel:
    table = Table(box=box.SIMPLE, show_header=False, expand=True)
    table.add_column("field", style="bold", no_wrap=True)
    table.add_column("value")
    order = [
        ("Username", "username"),
        ("Other usernames", "all_usernames"),
        ("Name", "full_name"),
        ("ID", "id"),
        ("Account created", "account_created_estimate"),
        ("Phone", "phone_number"),
        ("Bio", "bio"),
        ("Status", "status"),
        ("Last online", "last_online"),
        ("Language", "language_code"),
        ("Data center", "dc_id"),
        ("Premium", "is_premium"),
        ("Verified", "is_verified"),
        ("Bot", "is_bot"),
        ("Scam", "is_scam"),
        ("Fake", "is_fake"),
        ("Restricted", "is_restricted"),
        ("Has avatar", "has_photo"),
    ]
    optional = [
        ("Birthday", "birthday"),
        ("Avatars", "avatars_summary"),
        ("Has stories", "has_stories"),
        ("Personal channel", "personal_channel"),
        ("Business address", "business_address"),
        ("Business location", "business_geo"),
        ("Business hours TZ", "business_timezone"),
    ]
    for label, key in order:
        val = data.get(key)
        if key == "all_usernames" and isinstance(val, list):
            val = ", ".join(f"@{u}" for u in val) if val else None
        if isinstance(val, bool):
            val = "Yes" if val else "No"
        table.add_row(label, dwi_display_safe(val))
    for label, key in optional:
        val = data.get(key)
        if isinstance(val, bool):
            val = "Yes" if val else None
        if val in (None, "", []):
            continue
        table.add_row(label, dwi_display_safe(val))
    return Panel(table, title=f"[{style}]{title}[/]", border_style=style)


def dwi_message_to_row(msg: Any) -> dict[str, Any]:
    text = getattr(msg, "message", "") or ""
    sender = msg.sender
    if sender is None:
        sender_name = "Unknown"
    elif isinstance(sender, types.User):
        sender_name = sender.username or sender.first_name or "Unknown"
    else:
        sender_name = getattr(sender, "title", None) or "Unknown"
    fwd_name, fwd_id = dwi_forward_source(msg)
    return {
        "id": msg.id,
        "date": msg.date.isoformat() if msg.date else None,
        "sender_id": msg.sender_id,
        "sender": sender_name,
        "text": text,
        "media": dwi_media_label(msg),
        "views": getattr(msg, "views", None),
        "forwards": getattr(msg, "forwards", None),
        "forward_from": fwd_name,
        "forward_from_id": fwd_id,
    }


def dwi_analyse_messages(rows: list[dict[str, Any]]) -> dict[str, Any]:
    senders: Counter = Counter()
    per_day: Counter = Counter()
    hours: Counter = Counter()
    weekdays: Counter = Counter()
    domains: Counter = Counter()
    hashtags: Counter = Counter()
    mentions: Counter = Counter()
    words: Counter = Counter()
    media: Counter = Counter()
    fwd_sources: Counter = Counter()
    total_text = 0
    forwarded = 0

    for r in rows:
        if r["sender"]:
            senders[r["sender"]] += 1
        dt = None
        if r["date"]:
            try:
                dt = datetime.fromisoformat(r["date"])
            except ValueError:
                dt = None
        if dt is not None:
            per_day[dt.date().isoformat()] += 1
            hours[dt.hour] += 1
            weekdays[dt.weekday()] += 1
        if r["media"]:
            media[r["media"].split(".")[-1]] += 1
        if r.get("forward_from"):
            fwd_sources[r["forward_from"]] += 1
            forwarded += 1
        text = r["text"] or ""
        if text:
            total_text += 1
        for url in URL_RE.findall(text):
            host = urlparse(url).netloc.lower().removeprefix("www.")
            if host:
                domains[host] += 1
        for tag in HASHTAG_RE.findall(text):
            hashtags[tag.lower()] += 1
        for mention in MENTION_RE.findall(text):
            mentions[mention.lower()] += 1
        for word in WORD_RE.findall(text.lower()):
            if word not in _STOPWORDS:
                words[word] += 1

    return {
        "message_count": len(rows),
        "messages_with_text": total_text,
        "forwarded_count": forwarded,
        "top_senders": senders.most_common(15),
        "activity_by_day": dict(sorted(per_day.items())),
        "activity_by_hour": {h: hours.get(h, 0) for h in range(24)},
        "activity_by_weekday": {WEEKDAY_NAMES[d]: weekdays.get(d, 0) for d in range(7)},
        "top_domains": domains.most_common(15),
        "top_hashtags": hashtags.most_common(15),
        "top_mentions": mentions.most_common(15),
        "top_words": words.most_common(20),
        "top_forward_sources": fwd_sources.most_common(15),
        "media_breakdown": media.most_common(),
    }


def dwi_render_counter_table(title: str, rows: Iterable[tuple[str, int]], label: str) -> Table:
    t = Table(title=title, box=box.ROUNDED, title_style="bold")
    t.add_column(label)
    t.add_column("count", justify="right", style="cyan")
    any_row = False
    for name, count in rows:
        t.add_row(dwi_display_safe(name), str(count))
        any_row = True
    if not any_row:
        t.add_row("-", "0")
    return t


def dwi_render_sparkline(data: dict[Any, int], suffix: str = "") -> Text:
    if not data or not any(data.values()):
        return Text("no data", style="dim")
    bars = "▁▂▃▄▅▆▇█"
    values = list(data.values())
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    line = "".join(bars[min(len(bars) - 1, int((v - lo) / span * (len(bars) - 1)))] for v in values)
    return Text(f"{line}  {suffix}".rstrip(), style="green")


def dwi_render_activity_sparkline(by_day: dict[str, int]) -> Text:
    if not by_day:
        return Text("no dated messages", style="dim")
    return dwi_render_sparkline(by_day, f"({len(by_day)} days, peak {max(by_day.values())}/day)")


def dwi_hour_peak_label(by_hour: dict[int, int]) -> str:
    if not by_hour or not any(by_hour.values()):
        return ""
    peak = max(by_hour, key=by_hour.get)
    return f"peak {peak:02d}:00 UTC"


@dataclass
class Engine:
    client: TelegramClient
    me: dict[str, Any] = field(default_factory=dict)
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    photos: bool = False

    def _envelope(self, kind: str) -> dict[str, Any]:
        return {
            "type": kind,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "investigator": self.me,
        }

    async def whoami(self) -> dict[str, Any]:
        me = await self.client.get_me()
        self.me = dwi_extract_user(me)
        return self.me

    async def resolve_target(self, raw: str) -> tuple[str, Any]:
        kind, value = dwi_parse_target(raw)
        if kind == "invite":
            invite = await self.client(CheckChatInviteRequest(value))
            chat = getattr(invite, "chat", None)
            if chat is not None:
                return "entity", chat
            return "invite", invite
        if kind == "peer_channel":
            entity = await self.client.get_entity(types.PeerChannel(value))
            return "entity", entity
        entity = await self.client.get_entity(value)
        return "entity", entity

    def invite_preview_result(self, invite: Any) -> dict[str, Any]:
        if getattr(invite, "broadcast", False):
            kind = "Channel"
        elif getattr(invite, "megagroup", False):
            kind = "Megagroup"
        elif getattr(invite, "channel", False):
            kind = "Channel"
        else:
            kind = "Group"
        sample = []
        for u in getattr(invite, "participants", None) or []:
            sample.append({
                "id": getattr(u, "id", None),
                "username": getattr(u, "username", None),
                "name": " ".join(p for p in (getattr(u, "first_name", None),
                                             getattr(u, "last_name", None)) if p) or None,
            })
        return {
            **self._envelope("invite"),
            "invite": {
                "title": getattr(invite, "title", None),
                "about": getattr(invite, "about", None),
                "members_count": getattr(invite, "participants_count", None),
                "kind": kind,
                "request_needed": bool(getattr(invite, "request_needed", False)),
                "is_public": bool(getattr(invite, "public", False)),
                "is_verified": bool(getattr(invite, "verified", False)),
                "is_scam": bool(getattr(invite, "scam", False)),
                "is_fake": bool(getattr(invite, "fake", False)),
                "has_photo": getattr(invite, "photo", None) is not None
                             and not isinstance(getattr(invite, "photo", None), types.PhotoEmpty),
            },
            "sample_members": sample,
        }

    async def fetch_pinned(self, entity: Any, limit: int = 20) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            async for msg in self.client.iter_messages(
                entity, filter=types.InputMessagesFilterPinned, limit=limit
            ):
                rows.append(dwi_message_to_row(msg))
        except Exception:
            pass
        return rows

    async def enumerate_members(self, entity: Any, limit: int) -> dict[str, Any]:
        meta = {
            "id": getattr(entity, "id", None),
            "title": getattr(entity, "title", None),
            "username": getattr(entity, "username", None),
            "type": dwi_chat_type(entity),
            "members_count": getattr(entity, "participants_count", None),
        }
        members: list[dict[str, Any]] = []
        want = limit if (limit and limit > 0) else 50_000
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed} members"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Enumerating members…", total=None)
            try:
                async for user in self.client.iter_participants(entity, limit=want):
                    role, rank = dwi_participant_role(user)
                    members.append({
                        "id": user.id,
                        "username": user.username,
                        "name": " ".join(p for p in (user.first_name, user.last_name) if p) or None,
                        "phone": getattr(user, "phone", None),
                        "is_bot": bool(getattr(user, "bot", False)),
                        "is_premium": bool(getattr(user, "premium", False)),
                        "status": dwi_status_label(getattr(user, "status", None)),
                        "role": role,
                        "rank": rank,
                    })
                    if len(members) % 200 == 0:
                        progress.update(task, completed=len(members))
            except FloodWaitError as e:
                console.print(f"[yellow]Flood wait ({getattr(e, 'seconds', '?')}s); returning {len(members)} so far.[/]")
            except Exception as exc:
                console.print(f"[yellow]Member enumeration stopped: {exc}[/]")
            progress.update(task, completed=len(members))

        rank_order = {"creator": 0, "admin": 1, "member": 2}
        members.sort(key=lambda m: (rank_order.get(m["role"], 3), (m["username"] or "~").lower()))
        admins = [m for m in members if m["role"] in ("creator", "admin")]
        return {
            **self._envelope("members"),
            "chat": meta,
            "members": members,
            "admins": admins,
            "member_total": len(members),
        }

    async def archive_photos(self, entity: Any, label: str, download: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {"total": 0, "downloaded": 0, "first": None, "last": None, "paths": []}
        try:
            photos = await self.client.get_profile_photos(entity)
        except Exception as exc:
            if download:
                console.print(f"[yellow]Could not fetch profile photos: {exc}[/]")
            return out
        dates = sorted(p.date.isoformat() for p in photos if getattr(p, "date", None))
        out["total"] = len(photos)
        out["first"] = dates[0] if dates else None
        out["last"] = dates[-1] if dates else None
        if not download:
            return out
        if not photos:
            console.print("[dim]No profile photos visible.[/]")
            return out
        folder = EXPORT_DIR / "photos" / dwi_safe_filename(label)
        folder.mkdir(parents=True, exist_ok=True)
        console.print(f"[dim]Downloading {len(photos)} profile photo(s)…[/]")
        saved: list[Path] = []
        for i, photo in enumerate(photos):
            try:
                dest = folder / f"{i:03d}.jpg"
                path = await self.client.download_media(photo, file=str(dest))
                if path:
                    saved.append(Path(path))
            except Exception as exc:
                console.print(f"[yellow]Photo {i} failed: {exc}[/]")
        out["paths"] = saved
        out["downloaded"] = len(saved)
        return out

    async def collect_history(
        self,
        entity: Any,
        limit: int,
        from_user: Optional[Any] = None,
        keyword: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        base_kwargs: dict[str, Any] = {}
        if from_user is not None:
            base_kwargs["from_user"] = from_user
        if keyword:
            base_kwargs["search"] = keyword
        since = self.since
        want = limit if (limit and limit > 0) else 50_000
        offset_id = 0
        stop = False

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed} matched"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Fetching history…", total=None)
            while len(rows) < want and not stop:
                remaining = want - len(rows)
                call_kwargs = dict(base_kwargs)
                if offset_id == 0 and self.until is not None:
                    call_kwargs["offset_date"] = self.until
                try:
                    async for msg in self.client.iter_messages(
                        entity, limit=remaining, offset_id=offset_id, **call_kwargs
                    ):
                        offset_id = msg.id
                        if since is not None and msg.date is not None and msg.date < since:
                            stop = True
                            break
                        rows.append(dwi_message_to_row(msg))
                        if len(rows) % 200 == 0:
                            progress.update(task, completed=len(rows))
                    break
                except FloodWaitError as e:
                    wait = int(getattr(e, "seconds", 5))
                    progress.update(task, description=f"Flood wait {wait}s…")
                    await asyncio.sleep(wait)
                except ChannelPrivateError:
                    console.print("[red]Cannot access this chat's history (private or no access).[/]")
                    break
            progress.update(task, completed=len(rows))
        return rows

    async def profile_user(self, entity: Any, message_limit: int) -> dict[str, Any]:
        fu = None
        try:
            full = await self.client(GetFullUserRequest(entity))
            fu = full.full_user
        except Exception:
            pass
        user_data = dwi_extract_user(entity, fu)
        bio = user_data.get("bio")

        pc = None
        pcid = user_data.get("personal_channel_id")
        if pcid:
            try:
                ce = await self.client.get_entity(types.PeerChannel(pcid))
                handle = f"@{ce.username}" if getattr(ce, "username", None) else f"ID {pcid}"
                pc = {"id": pcid, "title": getattr(ce, "title", None),
                      "username": getattr(ce, "username", None)}
                user_data["personal_channel"] = f"{dwi_human(getattr(ce, 'title', None))} ({handle})"
            except Exception:
                pc = {"id": pcid, "title": None, "username": None}
                user_data["personal_channel"] = f"ID {pcid}"

        photos: list[str] = []
        avatars = await self.archive_photos(
            entity, user_data.get("username") or str(user_data.get("id")), download=self.photos
        )
        photos = [str(p) for p in avatars["paths"]]
        if avatars["total"]:
            first = (avatars["first"] or "")[:10]
            last = (avatars["last"] or "")[:10]
            if first and last and first != last:
                user_data["avatars_summary"] = f"{avatars['total']} ({first} → {last})"
            elif first:
                user_data["avatars_summary"] = f"{avatars['total']} ({first})"
            else:
                user_data["avatars_summary"] = str(avatars["total"])

        common: list[dict[str, Any]] = []
        footprint: list[dict[str, Any]] = []
        try:
            res = await self.client(
                GetCommonChatsRequest(user_id=entity, max_id=0, limit=100)
            )
            chats = res.chats
            for chat in chats:
                common.append({
                    "id": chat.id,
                    "title": getattr(chat, "title", None),
                    "type": dwi_chat_type(chat),
                })
            if message_limit != 0 and chats:
                console.print(f"[dim]Scanning {len(chats)} shared group(s) for the target's messages…[/]")
                per_group = message_limit if message_limit > 0 else 0
                for chat in chats:
                    msgs = await self.collect_history(chat, limit=per_group, from_user=entity)
                    for m in msgs:
                        m["group"] = getattr(chat, "title", None)
                    footprint.extend(msgs)
        except Exception as exc:
            console.print(f"[yellow]Common-group lookup limited: {exc}[/]")

        analytics = dwi_analyse_messages(footprint) if footprint else None
        timezone_info = dwi_infer_timezone(analytics["activity_by_hour"]) if analytics else None

        pivots = dwi_extract_pivots(bio)
        pivots["personal_channel"] = pc

        return {
            **self._envelope("user"),
            "subject": user_data,
            "common_chats": common,
            "message_footprint": footprint,
            "footprint_analytics": analytics,
            "timezone": timezone_info,
            "avatar_timeline": avatars,
            "pivots": pivots,
            "photos": photos,
        }

    async def profile_chat(self, entity: Any, limit: int) -> dict[str, Any]:
        description = None
        members_count = getattr(entity, "participants_count", None)
        linked_chat_id = None
        try:
            if isinstance(entity, types.Channel):
                full = await self.client(GetFullChannelRequest(entity))
                description = full.full_chat.about
                members_count = getattr(full.full_chat, "participants_count", members_count)
                linked_chat_id = getattr(full.full_chat, "linked_chat_id", None)
            elif isinstance(entity, types.Chat):
                full = await self.client(GetFullChatRequest(entity.id))
                description = full.full_chat.about
        except Exception:
            pass

        photos: list[str] = []
        if self.photos:
            label = getattr(entity, "username", None) or str(entity.id)
            arch = await self.archive_photos(entity, label, download=True)
            photos = [str(p) for p in arch["paths"]]

        meta = {
            "id": entity.id,
            "title": getattr(entity, "title", None),
            "username": getattr(entity, "username", None),
            "type": dwi_chat_type(entity),
            "description": description,
            "members_count": members_count,
            "is_verified": getattr(entity, "verified", None),
            "is_scam": getattr(entity, "scam", None),
            "is_restricted": getattr(entity, "restricted", None),
            "dc_id": getattr(getattr(entity, "photo", None), "dc_id", None),
            "linked_chat_id": linked_chat_id,
        }

        linked = None
        if linked_chat_id:
            try:
                le = await self.client.get_entity(types.PeerChannel(linked_chat_id))
                linked = {"id": linked_chat_id, "title": getattr(le, "title", None),
                          "username": getattr(le, "username", None)}
            except Exception:
                linked = {"id": linked_chat_id, "title": None, "username": None}

        pinned = await self.fetch_pinned(entity)
        rows = await self.collect_history(entity, limit=limit)
        analytics = dwi_analyse_messages(rows)
        pivots = dwi_extract_pivots(description, *[p["text"] for p in pinned])
        pivots["linked_chat"] = linked
        return {
            **self._envelope("chat"),
            "chat": meta,
            "analytics": analytics,
            "pinned": pinned,
            "pivots": pivots,
            "messages": rows,
            "photos": photos,
        }

    async def search_chat(self, entity: Any, keyword: str, limit: int) -> dict[str, Any]:
        rows = await self.collect_history(entity, limit=limit, keyword=keyword)
        return {
            **self._envelope("search"),
            "chat": {
                "id": entity.id,
                "title": getattr(entity, "title", None),
                "username": getattr(entity, "username", None),
            },
            "keyword": keyword,
            "matches": rows,
        }


def dwi_show_user_result(result: dict[str, Any]) -> None:
    console.print(dwi_render_user_panel(result["investigator"], "Logged-in Account", "magenta"))
    console.print(dwi_render_user_panel(result["subject"], "Target User", "red"))

    common = result["common_chats"]
    if common:
        t = Table(title="Shared Groups / Channels", box=box.ROUNDED)
        t.add_column("Title")
        t.add_column("Type", style="dim")
        t.add_column("ID", justify="right", style="cyan")
        for c in common:
            t.add_row(dwi_display_safe(c["title"]), c["type"].split(".")[-1], str(c["id"]))
        console.print(t)
    else:
        console.print("[dim]No shared groups (or none visible).[/]")

    fp = result["message_footprint"]
    if fp:
        console.print(f"\n[bold blue]Message footprint ({len(fp)} messages)[/]")
        for m in fp[:50]:
            grp = m.get("group", "")
            console.print(
                f"[green]{grp}[/] [dim]{m['date']}[/] "
                f"{m['sender']}: {m['text'] or '[media/file]'}"
            )
        if len(fp) > 50:
            console.print(f"[dim]… {len(fp) - 50} more (see export).[/]")

    a = result.get("footprint_analytics")
    if a and a["message_count"]:
        console.print(
            Panel(
                Group(
                    Text(f"Footprint: {a['message_count']} messages "
                         f"(with text: {a['messages_with_text']}, forwarded: {a['forwarded_count']})"),
                    Text.assemble(("Hour 00→23  ", "bold"),
                                  dwi_render_sparkline(a["activity_by_hour"], dwi_hour_peak_label(a["activity_by_hour"]))),
                    Text.assemble(("Weekday     ", "bold"),
                                  dwi_render_sparkline(a["activity_by_weekday"], "Mon→Sun")),
                ),
                title="Behavioural Profile", border_style="blue",
            )
        )
        tz = result.get("timezone")
        if tz:
            console.print(
                Panel.fit(
                    f"Likely timezone: [bold]{tz['label']}[/]  "
                    f"[dim](confidence {tz['confidence']}, {tz['samples']} msgs, "
                    f"quiet {tz['quiet_hours_utc']} UTC)[/]",
                    title="[blue]Timezone estimate[/]", border_style="blue",
                )
            )
        for title, key, lbl in [
            ("Top Words", "top_words", "word"),
            ("Top Domains Shared", "top_domains", "domain"),
            ("Top Hashtags", "top_hashtags", "#tag"),
            ("Mentions Made", "top_mentions", "@user"),
            ("Forwards From", "top_forward_sources", "source"),
            ("Media", "media_breakdown", "type"),
        ]:
            if a.get(key):
                console.print(dwi_render_counter_table(title, a[key], lbl))
    elif result.get("common_chats"):
        console.print(
            "[dim]Tip: set 'Messages per shared group' > 0 (or --messages N) to scan this "
            "target's posts across your shared groups for a behavioural profile + timezone estimate.[/]"
        )

    s = result["subject"]
    if s.get("is_bot") and (s.get("bot_commands") or s.get("bot_description")):
        blines: list[str] = []
        if s.get("bot_description"):
            blines.append(f"[bold]Description:[/] {escape(dwi_display_safe(s['bot_description']))}")
        if s.get("bot_privacy_url"):
            blines.append(f"[bold]Privacy policy:[/] {escape(str(s['bot_privacy_url']))}")
        for c in (s.get("bot_commands") or [])[:30]:
            blines.append(escape(str(c)))
        if blines:
            console.print(Panel("\n".join(blines), title="[red]Bot Info[/]", border_style="red"))

    pivots_panel = dwi_render_pivots(result.get("pivots"))
    if pivots_panel is not None:
        console.print(pivots_panel)


def dwi_show_chat_result(result: dict[str, Any]) -> None:
    meta = result["chat"]
    info = Table(box=box.SIMPLE, show_header=False)
    info.add_column("k", style="bold")
    info.add_column("v")
    for label, key in [
        ("Title", "title"), ("Username", "username"), ("Type", "type"),
        ("Members", "members_count"), ("DC", "dc_id"), ("ID", "id"),
        ("Verified", "is_verified"), ("Description", "description"),
    ]:
        info.add_row(label, dwi_display_safe(meta.get(key)))
    console.print(Panel(info, title="[cyan]Chat Intelligence[/]", border_style="cyan"))

    a = result["analytics"]
    console.print(
        Panel(
            Group(
                Text(f"Messages analysed: {a['message_count']}  "
                     f"(with text: {a['messages_with_text']}, forwarded: {a['forwarded_count']})"),
                dwi_render_activity_sparkline(a["activity_by_day"]),
            ),
            title="Overview", border_style="green",
        )
    )
    console.print(
        Panel(
            Group(
                Text.assemble(("Hour 00→23  ", "bold"),
                              dwi_render_sparkline(a["activity_by_hour"], dwi_hour_peak_label(a["activity_by_hour"]))),
                Text.assemble(("Weekday     ", "bold"),
                              dwi_render_sparkline(a["activity_by_weekday"], "Mon→Sun")),
            ),
            title="Posting Patterns", border_style="green",
        )
    )
    console.print(dwi_render_counter_table("Top Posters", a["top_senders"], "sender"))
    console.print(dwi_render_counter_table("Top Forward Sources", a["top_forward_sources"], "source"))
    console.print(dwi_render_counter_table("Top Domains Shared", a["top_domains"], "domain"))
    console.print(dwi_render_counter_table("Top Hashtags", a["top_hashtags"], "#tag"))
    console.print(dwi_render_counter_table("Top Mentions", a["top_mentions"], "@user"))
    console.print(dwi_render_counter_table("Top Words", a["top_words"], "word"))
    console.print(dwi_render_counter_table("Media Breakdown", a["media_breakdown"], "type"))

    pinned = result.get("pinned") or []
    if pinned:
        console.print(f"\n[bold blue]Pinned messages ({len(pinned)})[/]")
        for m in pinned[:10]:
            console.print(f"[dim]{m['date']}[/] {m['sender']}: {m['text'] or '[media/file]'}")

    pivots_panel = dwi_render_pivots(result.get("pivots"))
    if pivots_panel is not None:
        console.print(pivots_panel)


def dwi_show_members_result(result: dict[str, Any]) -> None:
    meta = result["chat"]
    console.print(
        Panel.fit(
            f"[bold]{escape(dwi_display_safe(meta.get('title')))}[/]  "
            f"[dim]{meta.get('type')} · {dwi_human(meta.get('members_count'))} members · "
            f"{result['member_total']} enumerated[/]",
            border_style="cyan", title="[cyan]Members[/]",
        )
    )
    admins = result["admins"]
    if admins:
        t = Table(title="Admins & Creator", box=box.ROUNDED, title_style="bold")
        t.add_column("Role")
        t.add_column("Username")
        t.add_column("Name")
        t.add_column("ID", justify="right", style="cyan")
        t.add_column("Rank", style="dim")
        for m in admins:
            t.add_row(
                m["role"],
                f"@{m['username']}" if m["username"] else "-",
                dwi_display_safe(m["name"]),
                str(m["id"]),
                dwi_display_safe(m["rank"]) if m["rank"] else "-",
            )
        console.print(t)
    else:
        console.print("[dim]No admins/creator visible.[/]")

    roster = result["members"]
    if roster:
        t = Table(title=f"Roster ({result['member_total']})", box=box.SIMPLE)
        t.add_column("Username")
        t.add_column("Name")
        t.add_column("ID", justify="right", style="cyan")
        t.add_column("Status", style="dim")
        t.add_column("Bot", style="dim")
        for m in roster[:200]:
            t.add_row(
                f"@{m['username']}" if m["username"] else "-",
                dwi_display_safe(m["name"]),
                str(m["id"]),
                dwi_human(m["status"]),
                "yes" if m["is_bot"] else "",
            )
        console.print(t)
        if result["member_total"] > 200:
            console.print(f"[dim]… {result['member_total'] - 200} more (see export).[/]")
    else:
        console.print("[yellow]No members enumerated (channel may hide its list, or you lack access).[/]")


def dwi_show_invite_result(result: dict[str, Any]) -> None:
    inv = result["invite"]
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("k", style="bold")
    table.add_column("v")
    for label, key in [
        ("Title", "title"), ("About", "about"), ("Members", "members_count"),
        ("Type", "kind"), ("Join request needed", "request_needed"),
        ("Public", "is_public"), ("Verified", "is_verified"), ("Scam", "is_scam"),
        ("Has photo", "has_photo"),
    ]:
        val = inv.get(key)
        if isinstance(val, bool):
            val = "Yes" if val else "No"
        table.add_row(label, dwi_display_safe(val))
    console.print(Panel(table, title="[yellow]Invite Preview (not joined)[/]", border_style="yellow"))

    sample = result.get("sample_members") or []
    if sample:
        console.print(f"[dim]{len(sample)} member(s) exposed in the invite preview:[/]")
        st = Table(box=box.SIMPLE)
        st.add_column("Username")
        st.add_column("Name")
        st.add_column("ID", justify="right", style="cyan")
        for s in sample:
            st.add_row(
                f"@{s['username']}" if s["username"] else "-",
                dwi_display_safe(s["name"]),
                str(s["id"]),
            )
        console.print(st)


def dwi_show_search_result(result: dict[str, Any]) -> None:
    matches = result["matches"]
    console.print(
        Panel.fit(
            f"[bold]{len(matches)}[/] matches for "
            f"[yellow]{result['keyword']}[/] in {dwi_display_safe(result['chat']['title'])}",
            border_style="blue",
        )
    )
    for m in matches[:80]:
        console.print(f"[dim]{m['date']}[/] {m['sender']}: {m['text']}")
    if len(matches) > 80:
        console.print(f"[dim]… {len(matches) - 80} more (see export).[/]")


def dwi_export_results(result: dict[str, Any], formats: list[str], label: str) -> list[Path]:
    EXPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = EXPORT_DIR / f"{dwi_safe_filename(label)}_{result['type']}_{stamp}"
    written: list[Path] = []

    if "json" in formats:
        p = base.with_suffix(".json")
        p.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        written.append(p)

    if "csv" in formats:
        if result["type"] == "members":
            rows = result.get("members") or []
            preferred = ["id", "username", "name", "phone", "role", "rank",
                         "status", "is_bot", "is_premium"]
        else:
            rows = (
                result.get("message_footprint")
                or result.get("messages")
                or result.get("matches")
                or []
            )
            preferred = [
                "id", "date", "group", "sender", "sender_id",
                "text", "media", "views", "forwards", "forward_from", "forward_from_id",
            ]
        if rows:
            p = base.with_suffix(".csv")
            present = set(rows[0].keys())
            fieldnames = [k for k in preferred if k in present]
            fieldnames += sorted(k for k in present if k not in preferred)
            with p.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            written.append(p)

    if "md" in formats:
        p = base.with_suffix(".md")
        p.write_text(dwi_build_markdown_report(result))
        written.append(p)

    return written


def dwi_markdown_pivots(pivots: Optional[dict[str, Any]]) -> list[str]:
    if not pivots:
        return []
    pc = pivots.get("personal_channel")
    linked = pivots.get("linked_chat")
    has_any = bool(pc or linked or any(pivots.get(k) for k in ("mentions", "tme_links", "socials")))
    if not has_any:
        return []
    out: list[str] = ["\n## Pivots\n"]
    if pc:
        handle = f"@{pc['username']}" if pc.get("username") else f"ID {pc.get('id')}"
        out.append(f"- **Personal channel:** {dwi_human(pc.get('title'))} ({handle})")
    if linked:
        handle = f"@{linked['username']}" if linked.get("username") else f"ID {linked.get('id')}"
        out.append(f"- **Linked discussion group:** {dwi_human(linked.get('title'))} ({handle})")
    for label, key in [("Mentions", "mentions"), ("Telegram links", "tme_links")]:
        if pivots.get(key):
            out.append(f"- **{label}:** {', '.join(pivots[key])}")
    if pivots.get("socials"):
        out.append("- **External:** " + ", ".join(f"{p}: {u}" for p, u in pivots["socials"]))
    return out


def dwi_build_markdown_report(result: dict[str, Any]) -> str:
    lines = [f"# Telegram OSINT Report - {result['type'].title()}",
             f"_Generated: {result['generated_at']}_\n"]
    inv = result.get("investigator", {})
    lines.append(f"**Investigator:** @{inv.get('username','?')} (ID {inv.get('id','?')})\n")

    if result["type"] == "user":
        s = result["subject"]
        skip = {"bot_commands", "personal_channel_id", "all_usernames"}
        lines.append("## Subject\n")
        if s.get("all_usernames"):
            lines.append(f"- **all_usernames:** {', '.join('@' + u for u in s['all_usernames'])}")
        for k, v in s.items():
            if k in skip:
                continue
            lines.append(f"- **{k}:** {dwi_human(v)}")
        av = result.get("avatar_timeline") or {}
        if av.get("total"):
            lines.append(f"- **avatars:** {av['total']} (first {dwi_human(av.get('first'))}, "
                         f"last {dwi_human(av.get('last'))})")
        if s.get("is_bot") and s.get("bot_commands"):
            lines.append("\n### Bot Commands\n")
            for c in s["bot_commands"]:
                lines.append(f"- `{c}`")
        if result.get("photos"):
            lines.append(f"\n**Profile photos saved:** {len(result['photos'])}")

        tz = result.get("timezone")
        a = result.get("footprint_analytics")
        if a and a["message_count"]:
            lines.append(f"\n## Behavioural Profile ({a['message_count']} messages, "
                         f"{a['forwarded_count']} forwarded)\n")
            if tz:
                lines.append(f"- **Likely timezone:** {tz['label']} "
                             f"(confidence {tz['confidence']}, {tz['samples']} msgs, "
                             f"quiet {tz['quiet_hours_utc']} UTC)")
            lines.append("\n### Posting by Hour (UTC)\n")
            for hour, count in a["activity_by_hour"].items():
                lines.append(f"- {int(hour):02d}:00 - {count}")
            for title, key in [
                ("Top Words", "top_words"), ("Top Domains", "top_domains"),
                ("Top Hashtags", "top_hashtags"), ("Mentions Made", "top_mentions"),
                ("Forwards From", "top_forward_sources"),
            ]:
                if a.get(key):
                    lines.append(f"\n### {title}\n")
                    for name, count in a[key]:
                        lines.append(f"- {name}: {count}")

        lines.append("\n## Shared Groups\n")
        for c in result["common_chats"] or [{"title": "-"}]:
            lines.append(f"- {c.get('title')} (ID {c.get('id','-')})")
        fp = result["message_footprint"]
        lines.append(f"\n## Message Footprint ({len(fp)})\n")
        for m in fp[:200]:
            lines.append(f"- `{m['date']}` **{m.get('group','')}** {m['sender']}: {dwi_oneline(m['text'])}")
        lines += dwi_markdown_pivots(result.get("pivots"))

    elif result["type"] == "chat":
        for k, v in result["chat"].items():
            lines.append(f"- **{k}:** {dwi_human(v)}")
        a = result["analytics"]
        lines.append(f"\n## Analytics ({a['message_count']} messages, "
                     f"{a['forwarded_count']} forwarded)\n")
        for title, key in [
            ("Top Posters", "top_senders"),
            ("Top Forward Sources", "top_forward_sources"),
            ("Top Domains", "top_domains"),
            ("Top Hashtags", "top_hashtags"),
            ("Top Mentions", "top_mentions"),
            ("Top Words", "top_words"),
        ]:
            lines.append(f"\n### {title}\n")
            for name, count in a[key] or [("-", 0)]:
                lines.append(f"- {name}: {count}")
        lines.append("\n### Posting by Hour (UTC)\n")
        for hour, count in a["activity_by_hour"].items():
            lines.append(f"- {int(hour):02d}:00 - {count}")
        lines.append("\n### Posting by Weekday\n")
        for day, count in a["activity_by_weekday"].items():
            lines.append(f"- {day}: {count}")
        pinned = result.get("pinned") or []
        if pinned:
            lines.append(f"\n## Pinned Messages ({len(pinned)})\n")
            for m in pinned[:50]:
                lines.append(f"- `{m['date']}` {m['sender']}: {dwi_oneline(m['text'])}")
        lines += dwi_markdown_pivots(result.get("pivots"))

    elif result["type"] == "members":
        m = result["chat"]
        lines.append(f"## {dwi_human(m.get('title'))} ({m.get('type')})\n")
        lines.append(f"- Members enumerated: {result['member_total']} / {dwi_human(m.get('members_count'))}\n")
        lines.append("### Admins & Creator\n")
        for a in result["admins"] or [{}]:
            rank = f" · {a['rank']}" if a.get("rank") else ""
            lines.append(f"- **{a.get('role','-')}** @{a.get('username') or '-'} "
                         f"{dwi_human(a.get('name'))} (ID {dwi_human(a.get('id'))}){rank}")
        lines.append("\n### Roster\n")
        for mem in result["members"][:1000]:
            lines.append(f"- @{mem.get('username') or '-'} {dwi_human(mem.get('name'))} "
                         f"(ID {mem['id']}) [{mem['role']}] {dwi_human(mem.get('status'))}")

    elif result["type"] == "invite":
        inv = result["invite"]
        lines.append("## Invite Preview (not joined)\n")
        for k in ("title", "about", "members_count", "kind", "request_needed",
                  "is_public", "is_verified", "is_scam", "has_photo"):
            lines.append(f"- **{k}:** {dwi_human(inv.get(k))}")
        sample = result.get("sample_members") or []
        if sample:
            lines.append(f"\n### Sample Members ({len(sample)})\n")
            for s in sample:
                lines.append(f"- @{s.get('username') or '-'} {dwi_human(s.get('name'))} (ID {dwi_human(s.get('id'))})")

    elif result["type"] == "search":
        lines.append(f"## Matches for `{result['keyword']}` ({len(result['matches'])})\n")
        for m in result["matches"][:300]:
            lines.append(f"- `{m['date']}` {m['sender']}: {dwi_oneline(m['text'])}")

    return "\n".join(lines) + "\n"


async def dwi_run(args: argparse.Namespace) -> None:
    if not args.no_clear:
        os.system("clear" if os.name == "posix" else "cls")
    dwi_print_banner()

    cfg = dwi_resolve_config(args)
    try:
        since = dwi_parse_date(args.since)
        until = dwi_parse_date(args.until)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        return

    client = TelegramClient(cfg.session_name, cfg.api_id, cfg.api_hash, flood_sleep_threshold=60)
    formats = [f.strip() for f in (args.export or "").split(",") if f.strip()]

    async with client:
        engine = Engine(client, since=since, until=until, photos=args.photos)
        await engine.whoami()
        console.print(f"[dim]Authenticated as @{engine.me.get('username')} "
                      f"(ID {engine.me.get('id')}).[/]")
        if since or until:
            console.print(f"[dim]Date window: {dwi_human(args.since)} → {dwi_human(args.until)} (UTC).[/]")
        console.print("")

        first = True
        while True:
            command, target, keyword, limit, messages = dwi_interactive_if_needed(
                args if first else dwi_fresh_operation_args()
            )
            first = False

            result = None
            try:
                kind, resolved = await engine.resolve_target(target)
                if kind == "invite":
                    result = engine.invite_preview_result(resolved)
                    dwi_show_invite_result(result)
                elif command == "user":
                    result = await engine.profile_user(resolved, messages)
                    dwi_show_user_result(result)
                elif command == "chat":
                    result = await engine.profile_chat(resolved, limit)
                    dwi_show_chat_result(result)
                elif command == "members":
                    result = await engine.enumerate_members(resolved, limit)
                    dwi_show_members_result(result)
                elif command == "search":
                    result = await engine.search_chat(resolved, keyword, limit)
                    dwi_show_search_result(result)
                else:
                    console.print("[red]Unknown command.[/]")
            except (ValueError, TypeError, UsernameInvalidError, UsernameNotOccupiedError):
                console.print(f"[red]'{target}' not found or not accessible.[/]")
            except Exception as exc:
                console.print(f"[red]Error: {exc}[/]")

            if result is not None and formats:
                written = dwi_export_results(result, formats, target)
                for p in written:
                    console.print(f"[green]Exported:[/] {p}")

            if result is not None and result.get("photos"):
                console.print(f"[green]Saved {len(result['photos'])} profile photo(s) → "
                              f"{EXPORT_DIR / 'photos'}/[/]")

            if args.once:
                break
            again = console.input("\n[green]Run another search? (Y/n): [/]").strip().lower()
            if again not in ("", "y", "yes"):
                console.print("[dim]Done.[/]")
                break


def dwi_fresh_operation_args() -> argparse.Namespace:
    return argparse.Namespace(command=None, target=None, keyword=None, limit=None, messages=None)


def dwi_interactive_if_needed(args: argparse.Namespace):
    command = args.command
    target = args.target
    keyword = getattr(args, "keyword", None)
    limit = args.limit
    messages = args.messages

    if not command:
        console.print("[bold]Choose an operation:[/]")
        console.print("  [cyan]1[/]) user    - profile a Telegram user")
        console.print("  [cyan]2[/]) chat    - analyse a group/channel")
        console.print("  [cyan]3[/]) members - list members + admins of a group")
        console.print("  [cyan]4[/]) search  - keyword search in a chat")
        choice = console.input("[green]> [/]").strip()
        command = {"1": "user", "2": "chat", "3": "members", "4": "search"}.get(choice, "user")

    if not target:
        target = console.input("[green]Target (@username / ID / link / invite): [/]").strip()

    if command == "user" and messages is None:
        raw = console.input("[green]Messages per shared group (0=none, -1=all): [/]").strip()
        messages = dwi_parse_int(raw, 0)
    if command == "chat" and limit is None:
        raw = console.input("[green]Messages to analyse [1000]: [/]").strip()
        limit = dwi_parse_int(raw, 1000)
    if command == "members" and limit is None:
        raw = console.input("[green]Members to fetch (0=all): [/]").strip()
        limit = dwi_parse_int(raw, 0)
    if command == "search":
        if not keyword:
            keyword = console.input("[green]Keyword: [/]").strip()
        if limit is None:
            raw = console.input("[green]Messages to scan [5000]: [/]").strip()
            limit = dwi_parse_int(raw, 5000)

    return command, target, keyword, (limit if limit is not None else 1000), (messages if messages is not None else 0)


def dwi_build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Telegram OSINT - Dark Web Informer edition.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("command", nargs="?", choices=["user", "chat", "members", "search"],
                   help="operation to run (omit for interactive menu)")
    p.add_argument("target", nargs="?", help="@username, numeric ID, t.me link, or invite link")
    p.add_argument("keyword", nargs="?", help="keyword (search command only)")

    p.add_argument("--api-id", type=int, help="Telegram API ID")
    p.add_argument("--api-hash", help="Telegram API hash")
    p.add_argument("--session", help="Telethon session name")

    p.add_argument("--limit", type=int, default=None,
                   help="messages to scan (chat/search) or members to fetch (members); 0 = all up to 50k")
    p.add_argument("--messages", type=int, default=None,
                   help="user: messages per shared group (0=none, -1=all)")
    p.add_argument("--since", help="only messages on/after this date (YYYY-MM-DD[ HH:MM], UTC)")
    p.add_argument("--until", help="only messages before this date (YYYY-MM-DD[ HH:MM], UTC)")
    p.add_argument("--photos", action="store_true",
                   help="download the target's profile-photo history (user/chat)")
    p.add_argument("--export", default="",
                   help="comma list of formats: json,csv,md")
    p.add_argument("--no-clear", action="store_true", help="don't clear the screen")
    p.add_argument("--once", action="store_true",
                   help="run one operation and exit instead of looping")
    return p


def dwi_main() -> None:
    args = dwi_build_parser().parse_args()
    try:
        asyncio.run(dwi_run(args))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/]")


if __name__ == "__main__":
    dwi_main()
