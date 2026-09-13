"""A phone view of a project, served over a private network.

The desktop app is where film gets logged, because logging is a keyboard
job and a grid on a touchscreen would be worse at it. But looking a play
up - every third and long, everything a given player was in, the last
four snaps before half - is something you want on a phone, standing on a
field, showing somebody.

It streams the *source* film rather than exported clips, and the page
seeks to a play's start and stops at its end. That decision came from
measuring a real project: 58 plays logged, zero exported, every
export_status "not_exported". A viewer that could only show exported
clips would have opened empty. This way every play a project has ever
logged is watchable the moment it is logged.

The socket binds every interface, because a phone reaches this over wifi
and over the tailnet and those are different adapters. Reach is limited
by who gets answered rather than by what is listened on: every request is
checked against `ALLOWED_CLIENT_NETWORKS` first, so a machine holding a
routable address, or sitting behind a forwarded port, serves nothing to
the internet even though the socket is up on it.

That is a floor, not a guarantee of privacy. Everyone on the same wifi is
inside those ranges too, and the read routes carry no token - anyone who
can reach the port can list the projects and stream the film. For access
from outside the house the answer is still a VPN like Tailscale, never a
forwarded port. SECURITY.md carries the full threat model.

Read-only unless `--allow-writes` is passed. With writes on, a phone can
set Run/Pass and clear the needs-review flag on a play that already
exists, and nothing else: no creating plays, no moving boundaries, no
free text. See `ProjectView.update_play` for why the line is drawn there
and `desktop_has_it_open` for the one thing that must be true first.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import os
import mimetypes
import re
import secrets
import shutil
import subprocess
import socket
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_PORT = 8733
#: The tag the detector and the desktop inspector both use to mark a play
#: a person still has to look at. Duplicated rather than imported so this
#: server stays standalone; a test asserts the two have not drifted.
REVIEW_TAG = "needs-review"
#: Everything a phone may put in `details_json["run_pass"]` - the desktop
#: inspector's own Run/Pass dropdown, plus "" to clear it. A phone gets a
#: closed set because the value drives chips, filters and the run/pass
#: research dataset; free text there is a desktop job.
ALLOWED_RUN_PASS = ("", "Run", "Pass", "RPO", "Screen")
#: The only keys a write request may carry. Enforced rather than assumed,
#: so widening the phone's reach past v1 has to be a deliberate edit here.
WRITABLE_KEYS = frozenset({
    "run_pass", "needs_review", "player_name", "other_players"})
#: A name longer than this is a paste accident, not a person. Names are
#: free text - an opponent's roster is usually unknown, so "#12 QB" has to
#: be allowed - but free text still needs an end.
MAX_NAME_LEN = 80
#: How many players one play may credit. High enough for a whole unit,
#: low enough that a runaway loop cannot bloat a project file.
MAX_INVOLVED = 24
#: Long enough to outlast a desktop autosave holding the write lock,
#: short enough that a phone gets an answer rather than a spinner.
WRITE_TIMEOUT_MS = 5000
#: A write body is two small fields. Anything larger is not one of ours.
MAX_BODY_BYTES = 4096
#: Plays per reel. High enough for every third down in a game, low enough
#: that one tap cannot hand FFmpeg an hour of work.
MAX_REEL_PLAYS = 60
#: Cut clips live here so a play shared twice is only cut once. Under the
#: system temp dir, because nothing in here is precious - the project and
#: the film are the originals.
CUT_DIR = Path(os.environ.get("TEMP", "/tmp")) / "tapesift-companion"
#: Chunk size for video streaming. Large enough to keep a phone's buffer
#: fed, small enough that a seek does not wait on a huge read.
CHUNK = 512 * 1024
#: The brand assets the page serves as its icon. iOS needs a raster image
#: for a homescreen tile - it ignores SVG there - while a browser tab
#: prefers the vector. Both ship in the repo already.
BRAND_DIR = Path(__file__).resolve().parent.parent / "resources" / "brand"
ICON_PNG = BRAND_DIR / "tapesift-app-icon-source.png"
ICON_SVG = BRAND_DIR / "tapesift-mark.svg"
#: What iOS asks for. The source art is 1254px square; serving that whole
#: file for a 180px tile wastes two megabytes on every homescreen add, so
#: it is scaled once and kept.
TOUCH_ICON_PX = 180
#: The networks this server will answer: loopback, the private and
#: link-local LAN ranges, and the CGNAT block Tailscale allocates from.
#:
#: The bind cannot be what limits reach - `serve` listens on every
#: interface on purpose, because wifi and the tailnet are different
#: adapters and a phone may arrive on either. This list is the limit
#: instead, and it is what stops a routable address or a forwarded port
#: from turning the companion into a public film server.
ALLOWED_CLIENT_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    "127.0.0.0/8",      # loopback
    "10.0.0.0/8",       # private
    "172.16.0.0/12",    # private
    "192.168.0.0/16",   # private
    "169.254.0.0/16",   # link-local
    "100.64.0.0/10",    # CGNAT, which is where Tailscale lives
    "::1/128",          # loopback
    "fc00::/7",         # unique local
    "fe80::/10",        # link-local
))


@dataclass
class Play:
    """One logged play, as a phone needs to see it."""

    number: int
    title: str
    start_ms: int
    end_ms: int
    tags: list[str] = field(default_factory=list)
    details: dict[str, str] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return max(0, self.end_ms - self.start_ms) / 1000.0

    def haystack(self) -> str:
        """Everything about this play, lowercased, for one search box.

        One box beats six filters on a phone. Typing "toney 3rd" should
        find it without deciding first which field either word lives in.
        """
        parts = [self.title, *self.tags, *(str(v) for v in self.details.values())]
        return " ".join(parts).lower()

    def as_json(self) -> dict:
        return {
            "number": self.number,
            "title": self.title,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "duration_s": round(self.duration_s, 1),
            "tags": self.tags,
            "details": self.details,
        }


def _json_field(raw: object, fallback):
    try:
        value = json.loads(raw) if raw else fallback
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def clean_player(text: object) -> str:
    """One player name, trimmed and bounded. Never raises on junk."""
    name = " ".join(str(text or "").split())
    return name[:MAX_NAME_LEN]


def clean_involved(value: object) -> list[str]:
    """The involved list: split, cleaned, de-duplicated, order kept.

    Accepts a list or a comma-separated string, because a phone field and
    a JSON client will not agree on which they send. Order is preserved
    rather than sorted - the order a person typed them in is information,
    and re-ordering their edit on save reads as the app arguing.
    """
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = []
    out: list[str] = []
    seen = set()
    for part in parts:
        name = clean_player(part)
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= MAX_INVOLVED:
            break
    return out


def token_file() -> Path:
    """Where the write token is remembered, beside settings.

    Deliberately not beside the projects: that folder gets shared, synced
    and screenshotted, and a token sitting next to the film is not a
    token.
    """
    from tapesift.core import paths
    return paths.app_data_dir() / "companion-token"


def stable_token(path: Path | None = None, *, rotate: bool = False) -> str:
    """The write token, remembered across restarts.

    A fresh token on every start meant every edit to this file
    invalidated the URL on the phone. A saved bookmark or homescreen icon
    would keep loading and quietly lose the ability to write, which reads
    as the whole thing being broken rather than as an expired credential.

    `rotate` mints a new one, which is how to revoke a URL that has been
    shared further than intended.
    """
    path = path or token_file()
    if not rotate:
        try:
            existing = path.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        if existing:
            return existing
    token = secrets.token_urlsafe(12)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
    except OSError:
        # Unwritable is survivable: this run still has a working token,
        # it just goes back to changing on restart.
        pass
    return token


def desktop_has_it_open(path: Path) -> bool:
    """True when some other process still has this project file open.

    SQLite in WAL mode - which `open_project_db` always sets - keeps a
    `-shm` file alongside the database for as long as any connection is
    open, and deletes it when the last one closes cleanly. Measured on
    Windows, not assumed: two connections open, one closed, file still
    there; last one closed, file gone.

    This matters more than ordinary locking does. `ProjectSession` holds
    the whole clip list in memory from the moment it opens, and its save
    is a replace-all - `DELETE FROM clips WHERE project_id=?` and then
    re-insert every clip from memory. A tag written from a phone while
    the desktop app is open survives until the next desktop save and is
    then gone without a word. No SQLite setting prevents that; WAL, busy
    timeouts and transactions all work exactly as intended and the edit
    still disappears. The only fix is not to write while it is open.

    A desktop that crashed leaves the file behind, so this can say yes
    when nothing is running. Opening and closing the project once in the
    desktop app clears it.
    """
    return path.with_name(path.name + "-shm").exists()


@dataclass
class ProjectLibrary:
    """Every project in a folder, so a phone is not stuck on one game.

    The server used to take a single project file, which meant the phone
    showed one game and the other nine in the same folder were invisible.
    A folder is the more natural unit: it is how the desktop app already
    organises projects and how a person thinks about their season.

    A single .tapesift path still works and yields a library of one, so
    nothing that already ran needs changing.
    """

    root: Path
    writable: bool = False

    def files(self) -> list[Path]:
        """Projects, newest first, skipping recovery snapshots.

        The desktop writes ".pre-stale-recovery-<stamp>.tapesift" beside a
        project when it repairs one. Those are backups, not games, and
        listing them puts a confusing near-duplicate next to the real one.
        """
        if self.root.is_file():
            return [self.root]
        found = [
            p for p in self.root.glob("*.tapesift")
            if ".pre-stale-recovery-" not in p.name
        ]
        return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)

    def views(self) -> list["ProjectView"]:
        return [ProjectView(p, writable=self.writable) for p in self.files()]

    def view_for(self, project_id: str) -> "ProjectView | None":
        """Look a project up by its id, refusing anything outside the root.

        The id is a filename stem that arrives from a URL, so it is
        untrusted: it is matched against the known list rather than joined
        onto a path, which is what stops "../../secrets" from resolving to
        a real file.
        """
        for path in self.files():
            if path.stem == project_id:
                return ProjectView(path, writable=self.writable)
        return None

    def default_view(self) -> "ProjectView | None":
        views = self.views()
        return views[0] if views else None

    def summaries(self) -> list[dict]:
        out = []
        for path in self.files():
            view = ProjectView(path)
            try:
                name, film = view.name_and_film()
                plays = len(view.plays())
            except sqlite3.Error:
                # A project mid-write, or one from an older schema. Listing
                # it as unreadable beats making the whole page fail.
                name, film, plays = path.stem, None, 0
            out.append({
                "id": path.stem,
                "name": name or path.stem,
                "plays": plays,
                "film_available": bool(film and film.is_file()),
            })
        return out


@dataclass
class ProjectView:
    """A project file, read fresh on every request.

    Deliberately not cached: the desktop app is writing to this database
    while the phone is reading it, and a stale list is worse than a
    slightly slower one. SQLite handles concurrent readers.
    """

    path: Path
    #: Off unless the operator asked for it on the command line. When it
    #: is off `_connect` is the only connection this class ever makes and
    #: it is `mode=ro`, so the old guarantee holds unchanged: a phone on
    #: the wifi cannot alter a project.
    writable: bool = False

    def _connect(self) -> sqlite3.Connection:
        # Read-only, so a phone can never damage a project.
        uri = f"file:{self.path.as_posix()}?mode=ro"
        return sqlite3.connect(uri, uri=True)

    def _connect_write(self) -> sqlite3.Connection:
        """A writer connection: autocommit, WAL, and willing to wait.

        `isolation_level=None` turns off the driver's implicit
        transactions so `update_play` can open its own IMMEDIATE one and
        have the read and the write inside the same lock.
        """
        db = sqlite3.connect(str(self.path), isolation_level=None,
                             timeout=WRITE_TIMEOUT_MS / 1000)
        db.execute(f"PRAGMA busy_timeout = {WRITE_TIMEOUT_MS}")
        # Already WAL for anything the desktop has opened; set here too so
        # a project that has only ever been read still gets readers that
        # do not block behind this write.
        db.execute("PRAGMA journal_mode = WAL")
        return db

    def name_and_film(self) -> tuple[str, Path | None]:
        # closing(), not the bare connection: `with sqlite3.connect(...)`
        # is a transaction block and leaves the handle open. Read handles
        # piling up is not just a leak - SQLite keeps the -shm file alive
        # for as long as any connection exists, so a long-lived server
        # ends up looking to `desktop_has_it_open` exactly like the
        # desktop app, and refuses its own writes.
        with contextlib.closing(self._connect()) as db:
            row = db.execute(
                "SELECT name, source_video_path FROM projects LIMIT 1"
            ).fetchone()
        if not row:
            return self.path.stem, None
        film = Path(row[1]) if row[1] else None
        return row[0] or self.path.stem, film

    def plays(self) -> list[Play]:
        with contextlib.closing(self._connect()) as db:
            rows = db.execute(
                "SELECT clip_number, clip_title, start_ms, end_ms, tags_json,"
                " details_json FROM clips WHERE enabled = 1"
                " ORDER BY order_index, clip_number"
            ).fetchall()
        plays = []
        for number, title, start, end, tags_raw, details_raw in rows:
            details = _json_field(details_raw, {})
            plays.append(Play(
                number=number or 0,
                title=title or f"Play {number}",
                start_ms=int(start or 0),
                end_ms=int(end or 0),
                tags=[str(t) for t in _json_field(tags_raw, [])],
                details={k: str(v) for k, v in details.items() if v},
            ))
        return plays

    def update_play(self, number: int, *, run_pass: str | None = None,
                    needs_review: bool | None = None,
                    player_name: str | None = None,
                    other_players: object = None) -> Play | None:
        """Set Run/Pass and the review flag on one existing play.

        Returns the play as it now stands, or None when no enabled clip
        carries that number. Arguments left at None are not touched, so a
        phone toggling the review flag cannot blank a run_pass it never
        sent.

        Read and write happen inside one IMMEDIATE transaction. Both
        fields live inside JSON columns, so every edit is a
        read-modify-write of the whole column: without the lock held
        across both halves, two phones toggling one clip at once would
        each read the old JSON and the second write would silently undo
        the first.

        This deliberately does not go through `ProjectSession`. That
        would load every clip, and its save is a replace-all that would
        write the phone's whole in-memory copy back over the file - which
        is exactly the clobber this is trying not to be on the other end
        of. One UPDATE against one row is the smaller, safer thing.
        """
        if not self.writable:
            raise PermissionError("This project is served read-only.")
        if run_pass is not None and run_pass not in ALLOWED_RUN_PASS:
            raise ValueError(
                "run_pass must be one of: "
                + ", ".join(repr(v) for v in ALLOWED_RUN_PASS))

        db = self._connect_write()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT rowid, tags_json, details_json FROM clips"
                " WHERE clip_number = ? AND enabled = 1"
                " ORDER BY order_index LIMIT 1", (number,)).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                return None
            rowid, tags_raw, details_raw = row
            tags = [str(t) for t in _json_field(tags_raw, [])]
            details = dict(_json_field(details_raw, {}))

            if run_pass is not None:
                # Clearing removes the key rather than storing "", which
                # is what the desktop's own metadata edit does and what
                # `plays()` assumes when it drops empty details.
                if run_pass:
                    details["run_pass"] = run_pass
                else:
                    details.pop("run_pass", None)
            if player_name is not None:
                name = clean_player(player_name)
                if name:
                    details["player_name"] = name
                else:
                    details.pop("player_name", None)
            if other_players is not None:
                names = clean_involved(other_players)
                if names:
                    # Stored the way the desktop inspector stores it, so
                    # the two screens read each other without translation.
                    details["other_players"] = ", ".join(names)
                else:
                    details.pop("other_players", None)
            if needs_review is not None:
                if needs_review and REVIEW_TAG not in tags:
                    tags.append(REVIEW_TAG)
                elif not needs_review:
                    tags = [t for t in tags if t != REVIEW_TAG]

            db.execute(
                "UPDATE clips SET tags_json = ?, details_json = ?,"
                " updated_at = ? WHERE rowid = ?",
                (json.dumps(tags), json.dumps(details),
                 datetime.now(timezone.utc).isoformat(), rowid))
            db.execute("COMMIT")
        except BaseException:
            try:
                db.execute("ROLLBACK")
            except sqlite3.Error:
                # Never let the rollback's complaint hide the real fault.
                pass
            raise
        finally:
            db.close()

        return next((p for p in self.plays() if p.number == number), None)


def client_is_local(address: str) -> bool:
    """Whether a client is on a network this server will answer.

    Never raises. An address that will not parse is not one of ours, which
    is the safe way to read anything arriving off a socket.
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    # A dual-stack listener reports IPv4 clients as ::ffff:192.168.1.5, and
    # that form matches none of the v4 networks unless it is unwrapped.
    mapped = getattr(parsed, "ipv4_mapped", None)
    if mapped is not None:
        parsed = mapped
    return any(parsed in network for network in ALLOWED_CLIENT_NETWORKS)


def _local_address() -> str:
    """The address a phone on the same wifi can actually reach."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is sent; this just asks the OS which interface it
        # would use, which is the one the phone can see.
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _tailscale_hosts() -> tuple[str, ...]:
    """Return private Tailscale names/IPs that can reach this computer.

    The companion already binds to every interface, including Tailscale;
    this is discovery, not a second server.  The CLI gives us the useful
    MagicDNS name and confirms that the tailnet is online.  ``ipconfig`` is
    a Windows fallback for installations where the service is connected
    but its status pipe is unavailable to this process.
    """
    candidates: list[str] = []
    seen_candidates: set[str] = set()

    def add_candidate(value: str) -> None:
        key = os.path.normcase(os.path.abspath(value))
        if key not in seen_candidates:
            seen_candidates.add(key)
            candidates.append(value)

    installed = shutil.which("tailscale")
    if installed:
        add_candidate(installed)
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        bundled = str(Path(program_files) / "Tailscale" / "tailscale.exe")
        if Path(bundled).is_file():
            add_candidate(bundled)

    hidden = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for executable in candidates:
        try:
            result = subprocess.run(
                [executable, "status", "--json"], capture_output=True,
                text=True, timeout=1.5, check=False, creationflags=hidden)
            if result.returncode:
                continue
            status = json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            continue

        own = status.get("Self") or {}
        if not own.get("Online", False):
            return ()
        hosts: list[str] = []
        dns_name = str(own.get("DNSName") or "").rstrip(".")
        if dns_name:
            hosts.append(dns_name)
        for value in own.get("TailscaleIPs") or ():
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                continue
            if address.version == 4:
                hosts.append(str(address))
        return tuple(dict.fromkeys(hosts))

    if os.name != "nt":
        return ()
    try:
        result = subprocess.run(
            ["ipconfig"], capture_output=True, text=True, timeout=3,
            check=False, creationflags=hidden)
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode:
        return ()

    # Tailscale IPv4 addresses live in the shared CGNAT range.  Filtering
    # keeps ordinary LAN, gateway and DNS addresses out of the phone URL.
    tailnet = ipaddress.ip_network("100.64.0.0/10")
    hosts = []
    for value in re.findall(
            r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])",
            result.stdout):
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address in tailnet:
            hosts.append(str(address))
    return tuple(dict.fromkeys(hosts))


def tailscale_urls(port: int, token: str = "") -> list[str]:
    """Build phone URLs for this machine's private Tailscale routes."""
    suffix = f"/?t={token}" if token else "/"
    return [f"http://{host}:{port}{suffix}" for host in _tailscale_hosts()]


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    """"bytes=1000-" -> (1000, size-1). None when unparseable.

    iOS will not play a video at all unless the server answers a range
    request with a 206 and a correct Content-Range. A plain 200 with the
    whole file gets a black screen and no error.
    """
    match = re.match(r"bytes=(\d*)-(\d*)", header or "")
    if not match:
        return None
    start_raw, end_raw = match.groups()
    if start_raw:
        start = int(start_raw)
        end = int(end_raw) if end_raw else size - 1
    elif end_raw:
        # A suffix range: the last N bytes.
        start = max(0, size - int(end_raw))
        end = size - 1
    else:
        return None
    if start >= size:
        return None
    return start, min(end, size - 1)


class CompanionHandler(BaseHTTPRequestHandler):
    library: ProjectLibrary = None  # set on the server class

    @property
    def project(self) -> ProjectView:
        """The project this request is about.

        Resolved per request from ?project=<id>, falling back to the most
        recently modified one so a bare URL opens something useful.
        """
        wanted = parse_qs(urlparse(self.path).query).get("project", [""])[0]
        if wanted:
            view = self.library.view_for(wanted)
            if view is not None:
                return view
        return self.library.default_view()
    token: str = ""  # set on the server class; empty means read-only
    server_version = "TapeSiftCompanion/1.0"

    def log_message(self, *args) -> None:  # noqa: D102 - quiet by default
        pass

    # ------------------------------------------------------------- routes

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        route = self.path.split("?")[0]
        try:
            if not self._client_allowed():
                self.send_error(403, "Not on a local network")
                return
            if route == "/":
                self._send_page()
            elif route == "/api/roster":
                self._send_roster()
            elif route == "/icon.png":
                self._send_touch_icon()
            elif route == "/icon.svg":
                self._send_file_bytes(ICON_SVG, "image/svg+xml")
            elif route == "/api/projects":
                self._send_bytes(
                    json.dumps({"projects": self.library.summaries()}
                               ).encode("utf-8"), "application/json")
            elif route == "/api/plays":
                self._send_plays()
            elif route == "/film":
                self._send_film()
            elif route.startswith("/clip/"):
                self._send_clip(route)
            elif route == "/reel":
                self._send_reel()
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            # A phone seeking or locking its screen drops the connection
            # mid-stream constantly. It is not an error worth reporting.
            pass

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        route = self.path.split("?")[0]
        try:
            if not self._client_allowed():
                self._send_json_error(403, "Not on a local network")
                return
            match = re.fullmatch(r"/api/play/(\d+)", route)
            if match:
                self._write_play(int(match.group(1)))
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -------------------------------------------------------------- auth

    def _client_allowed(self) -> bool:
        """Whether this request came from a network we serve.

        Checked before routing, on reads as well as writes: the read routes
        hand over the whole project and stream the film, so they are the
        ones that most need a caller to be nearby.
        """
        address = (self.client_address or ("",))[0]
        return client_is_local(str(address))

    def _authorised(self) -> bool:
        """Whether this request carries the token the server printed.

        A token in the URL is weak - it is visible in a screenshot and in
        anything that logs URLs - but the alternative on a home network
        is that every device on the wifi can rewrite logged film. It is
        the floor, not the ceiling: a confirmation on the desktop would
        be better and is the obvious next step.

        Accepted in a header or in `?t=`, because the phone opens the URL
        (query) and the page's own calls set the header.
        """
        if not self.token:
            return False
        supplied = self.headers.get("X-TapeSift-Token", "")
        if not supplied:
            query = parse_qs(urlparse(self.path).query)
            supplied = (query.get("t") or [""])[0]
        return secrets.compare_digest(supplied, self.token)

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The page and the play list, never the film. Both are small and
        # both change under the phone: the list every time somebody tags
        # something, and the page itself whenever this file is edited and
        # the server restarted. Safari caches a homescreen page hard
        # enough that a rebuilt UI can go unnoticed entirely, which is a
        # bad way to spend an afternoon. Video keeps its own path and
        # stays cacheable, which is where caching actually pays.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json_error(self, code: int, message: str) -> None:
        """A refusal a phone can display, rather than an HTML page.

        The read routes keep `send_error`, because a browser renders its
        HTML body fine. A write failure lands in a status line beside a
        button, where "why" is the whole content.
        """
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file_bytes(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Brand art does not change between restarts; a phone re-fetching
        # it on every page load is pure waste on a sideline connection.
        self.send_header("Cache-Control", "public, max-age=604800")
        self.end_headers()
        self.wfile.write(body)

    def _send_roster(self) -> None:
        """Names a phone can pick from, so a squad is not retyped by thumb.

        Two sources, in order. The names already used in this project are
        the best suggestions - they are spelled the way this analyst
        spells them, and they include the opponent labels no roster file
        carries. The bundled roster fills in the rest of the squad.

        Free text still wins on the phone. This is a convenience list, not
        a closed set: an opponent is usually "#12 QB" and no roster has
        that.
        """
        seen: dict[str, str] = {}

        def remember(name: str) -> None:
            cleaned = clean_player(name)
            if cleaned:
                seen.setdefault(cleaned.casefold(), cleaned)

        view = self.project
        if view is not None:
            for play in view.plays():
                remember(play.details.get("player_name", ""))
                for part in str(
                        play.details.get("other_players", "")).split(","):
                    remember(part)

        used = sorted(seen.values(), key=str.casefold)
        squad: list[str] = []
        try:
            from tapesift.services.roster_service import load_bundled
            roster = load_bundled("miami_2026")
            for player in sorted(
                    roster,
                    key=lambda p: (int(p.number) if p.number.isdigit() else 999,
                                   p.name)):
                label = f"{player.number} {player.name}".strip()
                if label.casefold() not in seen:
                    squad.append(label)
        except Exception:
            # A missing or unreadable roster is not a reason to fail the
            # request - the used-names half is the more useful half.
            squad = []

        self._send_bytes(
            json.dumps({"used": used, "squad": squad}).encode("utf-8"),
            "application/json")

    def _send_touch_icon(self) -> None:
        """The homescreen tile, scaled once and cached on disk.

        Scaling needs Qt, which the desktop app already depends on but
        which a headless server has no business requiring. If it is not
        importable, or fails, the full-size source is served instead - a
        heavier icon beats no icon.
        """
        cached = CUT_DIR / f"icon-{TOUCH_ICON_PX}.png"
        if not cached.exists():
            CUT_DIR.mkdir(parents=True, exist_ok=True)
            try:
                from PySide6.QtGui import QGuiApplication, QImage
                if QGuiApplication.instance() is None:
                    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
                    QGuiApplication([])
                image = QImage(str(ICON_PNG))
                if not image.isNull():
                    from PySide6.QtCore import Qt
                    image.scaled(
                        TOUCH_ICON_PX, TOUCH_ICON_PX,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ).save(str(cached), "PNG")
            except Exception:
                pass
        self._send_file_bytes(
            cached if cached.exists() else ICON_PNG, "image/png")

    def _send_page(self) -> None:
        view = self.project
        name = view.name_and_film()[0] if view else "No projects found"
        self._send_bytes(PAGE.replace("{{PROJECT}}", name).encode("utf-8"),
                         "text/html; charset=utf-8")

    def _send_plays(self) -> None:
        if self.project is None:
            self._send_bytes(json.dumps(
                {"project": "", "plays": [], "film_available": False,
                 "writable": False}).encode("utf-8"), "application/json")
            return
        name, film = self.project.name_and_film()
        payload = {
            "project": name,
            "film_available": bool(film and film.is_file()),
            # The page shows edit controls off this, so it answers "can
            # this request write", not "was the server started with
            # writes on". A phone that opened the URL without the token
            # gets exactly the read-only view it got before any of this.
            "writable": bool(self.project.writable and self._authorised()),
            "run_pass_values": [v for v in ALLOWED_RUN_PASS if v],
            "review_tag": REVIEW_TAG,
            "plays": [p.as_json() for p in self.project.plays()],
        }
        self._send_bytes(json.dumps(payload).encode("utf-8"),
                         "application/json")

    def _write_play(self, number: int) -> None:
        """Apply one phone edit to one play. The whole write surface."""
        if not self.project.writable:
            self._send_json_error(405, "This project is served read-only")
            return
        if not self._authorised():
            # 403, not 401: there is no login to send anybody to. The
            # token either came with the URL or the URL was guessed.
            self._send_json_error(403, "Missing or bad token")
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self._send_json_error(413, "Body too large")
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as exc:
            self._send_json_error(400, f"Bad JSON: {exc}")
            return
        if not isinstance(body, dict):
            self._send_json_error(400, "Body must be a JSON object")
            return

        unknown = sorted(set(body) - WRITABLE_KEYS)
        if unknown:
            self._send_json_error(
                400, "A phone may only set "
                     f"{', '.join(sorted(WRITABLE_KEYS))} - got "
                     f"{', '.join(unknown)}")
            return
        needs_review = body.get("needs_review")
        if needs_review is not None and not isinstance(needs_review, bool):
            self._send_json_error(400, "needs_review must be true or false")
            return
        run_pass = body.get("run_pass")
        if run_pass is not None and not isinstance(run_pass, str):
            self._send_json_error(400, "run_pass must be a string")
            return

        player_name = body.get("player_name")
        if player_name is not None and not isinstance(player_name, str):
            self._send_json_error(400, "player_name must be a string")
            return

        # A list from a JSON client, a comma-separated string from the
        # phone field. Both are normalised in clean_involved; anything
        # else is a bug in the caller and should say so.
        other = body.get("other_players")
        if other is not None and not isinstance(other, (str, list)):
            self._send_json_error(
                400, "other_players must be a string or a list of names")
            return

        try:
            play = self.project.update_play(
                number, run_pass=run_pass, needs_review=needs_review,
                player_name=player_name, other_players=other)
        except ValueError as exc:
            self._send_json_error(400, str(exc))
            return
        except sqlite3.OperationalError as exc:
            # Nearly always "database is locked": something else held the
            # write lock for longer than WRITE_TIMEOUT_MS. Retrying is
            # the phone's call, so say so rather than swallowing it.
            self._send_json_error(503, f"Project busy, try again: {exc}")
            return
        if play is None:
            self._send_json_error(404, f"No enabled play numbered {number}")
            return
        self._send_bytes(json.dumps(play.as_json()).encode("utf-8"),
                         "application/json")

    def _send_reel(self) -> None:
        """Cut several plays and hand back one file.

        Saving a cut-up one play at a time is the thing the phone was
        worst at: a browser cannot be asked for forty downloads, and a
        person cannot share forty files. One reel is one thing to send.

        Each play is cut through the same cache the single-clip route
        uses, so a play already saved is not cut twice, and the join is
        a stream copy - no re-encode, seconds not minutes.
        """
        query = parse_qs(urlparse(self.path).query)
        raw = (query.get("plays") or [""])[0]
        wanted: list[int] = []
        for piece in raw.split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                wanted.append(int(piece))
            except ValueError:
                self.send_error(400, f"Not a play number: {piece}")
                return
        if not wanted:
            self.send_error(400, "No plays asked for")
            return
        if len(wanted) > MAX_REEL_PLAYS:
            self.send_error(
                413, f"A reel is capped at {MAX_REEL_PLAYS} plays; "
                     f"{len(wanted)} were asked for")
            return

        view = self.project
        if view is None:
            self.send_error(404, "No such project")
            return
        by_number = {p.number: p for p in view.plays()}
        missing = [n for n in wanted if n not in by_number]
        if missing:
            self.send_error(
                404, "No play numbered " + ", ".join(str(n) for n in missing))
            return
        # Film order, not tap order: a reel that jumps around is harder to
        # read than one that runs the way the game did.
        plays = sorted((by_number[n] for n in wanted),
                       key=lambda p: (p.start_ms, p.number))

        _, film = view.name_and_film()
        if not film or not film.is_file():
            self.send_error(404, "Source film not found on this machine")
            return

        target = self._reel_path(film, plays)
        if not target.exists() and not self._build_reel(film, plays, target):
            self.send_error(
                503, "Could not build this reel - is FFmpeg available?")
            return
        stem = re.sub(r"[^A-Za-z0-9]+", "-", view.path.stem).strip("-")
        self._stream_file(
            target, download_name=f"{stem or 'reel'}-{len(plays)}-plays.mp4")

    def _reel_path(self, film: Path, plays: list[Play]) -> Path:
        """Keyed on the film and every range in it, so an edit re-cuts."""
        stamp = f"{film}|{film.stat().st_mtime_ns}|" + "|".join(
            f"{p.number}:{p.start_ms}-{p.end_ms}" for p in plays)
        digest = hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:16]
        CUT_DIR.mkdir(parents=True, exist_ok=True)
        return CUT_DIR / f"reel-{digest}.mp4"

    def _build_reel(self, film: Path, plays: list[Play], target: Path) -> bool:
        ffmpeg = shutil.which("ffmpeg") or ""
        if not ffmpeg:
            try:
                from tapesift.services import ffmpeg_service
                ffmpeg = ffmpeg_service.find_executable("ffmpeg")
            except Exception:
                return False
        if not ffmpeg:
            return False

        pieces = []
        for play in plays:
            cut = self._cut_path(film, play)
            if not cut.exists() and not self._extract(film, play, cut):
                return False
            pieces.append(cut)

        listing = target.with_suffix(".txt")
        # The concat demuxer wants single quotes doubled inside the path.
        listing.write_text(
            "".join(f"file '{str(p).replace(chr(39), chr(39) * 2)}'\n"
                    for p in pieces), encoding="utf-8")
        partial = target.with_suffix(".partial.mp4")
        command = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", "-movflags", "+faststart", str(partial),
        ]
        try:
            done = subprocess.run(command, capture_output=True, timeout=600)
        except (OSError, subprocess.SubprocessError):
            partial.unlink(missing_ok=True)
            listing.unlink(missing_ok=True)
            return False
        finally:
            listing.unlink(missing_ok=True)
        if done.returncode != 0 or not partial.exists():
            partial.unlink(missing_ok=True)
            return False
        partial.replace(target)
        return True

    def _send_film(self) -> None:
        _, film = self.project.name_and_film()
        if not film or not film.is_file():
            self.send_error(404, "Source film not found on this machine")
            return
        size = film.stat().st_size
        content_type = mimetypes.guess_type(film.name)[0] or "video/mp4"
        span = _parse_range(self.headers.get("Range", ""), size)

        if span is None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            start, end = 0, size - 1
        else:
            start, end = span
            self.send_response(206)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        remaining = end - start + 1
        with film.open("rb") as handle:
            handle.seek(start)
            while remaining > 0:
                block = handle.read(min(CHUNK, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)


    def _send_clip(self, route: str) -> None:
        """Cut one play out of the film so it can be saved or shared.

        Streaming the film with a seek is right for watching, but a person
        cannot send someone "the film, start at 12:04". Sharing needs a
        file that is only the play.

        The cut is copied, not re-encoded - a few seconds of H.264 out of
        an existing H.264 file takes well under a second and loses nothing.
        The trade is that the cut lands on the nearest keyframe before the
        requested start, so it can open up to a second early. For showing
        somebody a rep that is the right trade against making a phone wait
        for an encode.
        """
        try:
            number = int(route.rsplit("/", 1)[-1].removesuffix(".mp4"))
        except ValueError:
            self.send_error(404)
            return

        play = next((p for p in self.project.plays() if p.number == number),
                    None)
        _, film = self.project.name_and_film()
        if play is None or not film or not film.is_file():
            self.send_error(404)
            return

        cut = self._cut_path(film, play)
        if not cut.exists() and not self._extract(film, play, cut):
            self.send_error(
                503, "Could not cut this play - is FFmpeg available?")
            return
        self._stream_file(cut, download_name=self._download_name(play))

    @staticmethod
    def _download_name(play: Play) -> str:
        """What the file is called when it lands in somebody's photos."""
        bits = [f"{play.number:03d}"]
        for key in ("quarter", "down_distance", "run_pass"):
            if play.details.get(key):
                bits.append(str(play.details[key]))
        safe = re.sub(r"[^A-Za-z0-9]+", "-", " ".join(bits)).strip("-")
        return f"{safe or play.number}.mp4"

    def _cut_path(self, film: Path, play: Play) -> Path:
        """Keyed on film, range and mtime, so an edited range re-cuts."""
        stamp = f"{film}|{film.stat().st_mtime_ns}|{play.start_ms}|{play.end_ms}"
        digest = hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:16]
        CUT_DIR.mkdir(parents=True, exist_ok=True)
        return CUT_DIR / f"{digest}.mp4"

    @staticmethod
    def _extract(film: Path, play: Play, target: Path) -> bool:
        ffmpeg = shutil.which("ffmpeg") or ""
        if not ffmpeg:
            try:
                from tapesift.services import ffmpeg_service
                ffmpeg = ffmpeg_service.find_executable("ffmpeg")
            except Exception:
                return False
        if not ffmpeg:
            return False
        partial = target.with_suffix(".partial.mp4")
        command = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            # -ss before -i seeks by keyframe and is near-instant.
            "-ss", f"{play.start_ms / 1000:.3f}",
            "-i", str(film),
            "-t", f"{max(0.1, (play.end_ms - play.start_ms) / 1000):.3f}",
            "-c", "copy",
            # Needed for a phone to play a copied stream inline.
            "-movflags", "+faststart",
            str(partial),
        ]
        try:
            done = subprocess.run(command, capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            partial.unlink(missing_ok=True)
            return False
        if done.returncode != 0 or not partial.exists():
            partial.unlink(missing_ok=True)
            return False
        partial.replace(target)
        return True

    def _stream_file(self, path: Path, download_name: str = "") -> None:
        size = path.stat().st_size
        span = _parse_range(self.headers.get("Range", ""), size)
        if span is None:
            self.send_response(200)
            start, end = 0, size - 1
            self.send_header("Content-Length", str(size))
        else:
            start, end = span
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        if download_name:
            self.send_header(
                "Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        remaining = end - start + 1
        with path.open("rb") as handle:
            handle.seek(start)
            while remaining > 0:
                block = handle.read(min(CHUNK, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)


def serve(project_path: Path, port: int = DEFAULT_PORT,
          host: str = "0.0.0.0", *, writable: bool = False,
          token: str = "") -> tuple[ThreadingHTTPServer, str]:
    """Start the companion. Returns the server and the phone's URL.

    With `writable`, the returned URL carries a token - that URL is the
    editing one, and the same address without it is the read-only view.
    Pass `token` to choose it; `main` passes the saved one so a phone's
    bookmark survives a restart, and revoking is then `--new-token`
    rather than just stopping the server. Left empty, a throwaway is
    minted that does die with the process.

    Callers turning this on are responsible for checking
    `desktop_has_it_open` first; `main` does.

    Pass `port=0` for whatever the OS has free; the returned URL always
    names the port actually bound rather than the one asked for, so it
    stays the truth either way.

    `host` stays every interface by default and should be left alone.
    Binding one address looks tighter and is not: wifi and the tailnet are
    different adapters, so picking either one silently drops the other,
    and `_local_address()` moves whenever DHCP feels like it. What limits
    reach is `ALLOWED_CLIENT_NETWORKS`, applied per request in
    `_client_allowed` - a routable client is refused whatever the socket
    happens to be listening on.

    The caller owns the server. Stopping it properly is `shutdown()` and
    then `server_close()` - `shutdown()` alone only ends the request loop
    and leaves the socket listening, which on Windows lets a later bind
    quietly share the port and abort connections mid-request.
    """
    # A caller that supplies one gets it honoured; `main` passes the saved
    # token so a phone's bookmark survives a restart. Anything else gets a
    # throwaway that dies with the process.
    token = (token or secrets.token_urlsafe(12)) if writable else ""
    handler = type("BoundHandler", (CompanionHandler,), {
        "library": ProjectLibrary(Path(project_path), writable=writable),
        "token": token,
    })
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{_local_address()}:{httpd.server_address[1]}/"
    if token:
        url += f"?t={token}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, url


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>{{PROJECT}}</title>
<link rel="icon" type="image/svg+xml" href="icon.svg">
<link rel="apple-touch-icon" href="icon.png">
<meta name="apple-mobile-web-app-title" content="TapeSift">
<meta name="theme-color" content="#0c0f0d">
<style>
  /* Lifted from the desktop app rather than approximated: ui_v2/theme.py
     for the surfaces and the brand green, ui_v2/tag_readout.py for the play
     types. The split there matters and is kept here - #39e07a is the
     primary action (Save, Sync, the jog dial) and never labels data, while
     RUN gets the softer #57c98a so a Run marker and a Sync button do not
     read as the same thing. */
  :root {
    color-scheme: dark;
    --bg:#0c0f0d; --surface:#171815; --raised:#1f201c; --hover:#2b2b24;
    --line:#2a352d; --line2:#3b3b32;
    --text:#f2f5f2; --dim:#b8c2ba; --faint:#748078;
    --green:#39e07a; --green-hi:#62ec98; --green-dim:#25412b;
    --green-dark:#0f1710;
    --run:#57c98a; --pass:#4da3ff; --rpo:#36d6c0; --screen:#9567d8;
    --review:#f17d72; --warn:#f0c46a;
    --tap:44px;
    --i-list:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round'%3E%3Cpath d='M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01'/%3E%3C/svg%3E");
    --i-cut:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round'%3E%3Ccircle cx='6' cy='6' r='3'/%3E%3Ccircle cx='6' cy='18' r='3'/%3E%3Cpath d='M20 4L8.7 15.3M14.5 14.5L20 20M8.7 8.7L12 12'/%3E%3C/svg%3E");
    --i-grid:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='2' stroke-linecap='round'%3E%3Crect x='4' y='4' width='6' height='6' rx='1'/%3E%3Crect x='14' y='4' width='6' height='6' rx='1'/%3E%3Crect x='4' y='14' width='6' height='6' rx='1'/%3E%3Crect x='14' y='14' width='6' height='6' rx='1'/%3E%3C/svg%3E");
  }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body {
    margin:0; background:var(--bg); color:var(--text);
    font:15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    padding-bottom:env(safe-area-inset-bottom);
    -webkit-font-smoothing:antialiased;
  }
  /* The player is a sheet now rather than a block above the list. It is
     over 600px tall with the jog and tag rows on it, which is most of a
     phone - so it takes the screen while you work a play and gives all
     of it back when you are done. Nothing competes for the same space. */
  #sheet { position:fixed; inset:0; z-index:60; background:var(--bg);
           display:flex; flex-direction:column; overflow-y:auto;
           transform:translateY(100%); visibility:hidden;
           transition:transform .22s ease, visibility .22s; }
  #sheet.on { transform:translateY(0); visibility:visible; }
  #grab { position:sticky; top:0; z-index:2; display:flex; align-items:center;
          justify-content:center; padding:7px 0 6px; background:var(--bg);
          border-bottom:1px solid var(--line); }
  #grab span { width:38px; height:4px; border-radius:99px;
               background:var(--line2); }
  #sheetclose { position:absolute; right:6px; top:0; width:38px; height:38px;
                border:0; background:transparent; color:var(--dim);
                font-size:23px; line-height:1; }
  #player { background:#000; }
  /* One screen in the flow at a time, so the list gets the whole viewport
     instead of whatever the player left over. */
  .screen { display:none;
            padding-bottom:calc(58px + env(safe-area-inset-bottom)); }
  .screen.on { display:block; }
  .shead { display:flex; align-items:baseline; gap:8px; padding:14px 14px 10px;
           border-bottom:1px solid var(--line); }
  .shead b { font-size:16px; font-weight:600; }
  .shead span { margin-left:auto; color:var(--faint); font-size:12px;
                font-variant-numeric:tabular-nums; }
  #tabs { position:fixed; left:0; right:0; bottom:0; z-index:50; display:flex;
          background:var(--surface); border-top:1px solid var(--line);
          padding-bottom:env(safe-area-inset-bottom); }
  #tabs .tab { flex:1; min-width:0; height:58px; border:0; background:none;
               color:var(--faint); font-family:inherit; font-size:11px;
               font-weight:600; display:flex; flex-direction:column;
               align-items:center; justify-content:center; gap:3px;
               position:relative; }
  #tabs .tab.on { color:var(--green); }
  #tabs .tab i { width:20px; height:20px; display:block;
                 background:currentColor;
                 -webkit-mask-repeat:no-repeat; mask-repeat:no-repeat;
                 -webkit-mask-position:center; mask-position:center;
                 -webkit-mask-size:contain; mask-size:contain; }
  #tabs .tab[data-s="plays"] i { -webkit-mask-image:var(--i-list);
                                 mask-image:var(--i-list); }
  #tabs .tab[data-s="export"] i { -webkit-mask-image:var(--i-cut);
                                  mask-image:var(--i-cut); }
  #tabs .tab[data-s="games"] i { -webkit-mask-image:var(--i-grid);
                                 mask-image:var(--i-grid); }
  #tabs .tab b { position:absolute; top:8px; left:50%; margin-left:4px;
                 min-width:17px; height:17px; padding:0 4px; border-radius:99px;
                 background:var(--green); color:var(--green-dark);
                 font-size:10px; line-height:17px; font-weight:600; }
  .xbar { display:flex; gap:6px; padding:10px 14px; }
  .xbar button { flex:1; min-width:0; height:32px; border-radius:8px;
                 font-family:inherit; font-size:12px; font-weight:600;
                 border:1px solid var(--line2); background:var(--raised);
                 color:var(--dim); white-space:nowrap; }
  .xbar button:active { background:var(--hover); color:var(--text); }
  #xlist { list-style:none; margin:0; padding:0 0 76px; }
  #xlist li { display:flex; align-items:center; gap:11px; padding:11px 14px;
              border-bottom:1px solid var(--line); min-height:var(--tap); }
  #xlist .box { flex:none; width:21px; height:21px; border-radius:6px;
                border:1.5px solid var(--line2); }
  #xlist li[data-on="1"] .box { background:var(--green);
                                border-color:var(--green); }
  #xlist .xt { flex:1; min-width:0; font-size:14px;
               white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  #xlist .xd { flex:none; color:var(--faint); font-size:12px;
               font-variant-numeric:tabular-nums; }
  #xfoot { position:fixed; left:0; right:0;
           bottom:calc(58px + env(safe-area-inset-bottom)); z-index:40;
           display:flex; align-items:center; gap:10px; padding:10px 14px;
           background:var(--surface); border-top:1px solid var(--line); }
  #xmsg { flex:1; min-width:0; font-size:12px; color:var(--dim); }
  .xbtn { flex:none; height:38px; padding:0 16px; border-radius:9px;
          display:grid; place-items:center; font-size:13px; font-weight:600;
          font-family:inherit; text-decoration:none;
          border:1px solid var(--green-dim);
          background:var(--green-dark); color:var(--green); }
  .xbtn[data-ready="1"] { background:var(--green); color:var(--green-dark);
                          border-color:var(--green); }
  .xbtn[aria-disabled="true"] { opacity:.38; }
  #proj { display:block; width:calc(100% - 28px); margin:12px 14px;
          height:var(--tap); padding:0 12px; font-size:15px;
          font-family:inherit; border-radius:10px;
          border:1px solid var(--line); background:var(--surface);
          color:var(--text); }
  #glist { list-style:none; margin:0; padding:0; }
  #glist li { display:flex; align-items:center; gap:10px; padding:12px 14px;
              border-bottom:1px solid var(--line); }
  #glist li[data-on="1"] { background:rgba(57,224,122,.07);
                           box-shadow:inset 3px 0 0 var(--green); }
  #glist .gn { flex:1; min-width:0; font-size:14px; }
  #glist .gc { flex:none; color:var(--faint); font-size:12px;
               font-variant-numeric:tabular-nums; }
  #glist .gz { flex:none; color:var(--warn); font-size:11px; }
  video { width:100%; display:block; background:#000; max-height:34vh; }
  #bar { display:flex; align-items:center; gap:8px; padding:9px 12px;
         background:var(--surface); border-bottom:1px solid var(--line); }
  #np { flex:1; min-width:0; }
  #np b { display:block; font-size:14px; font-weight:600;
          white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  #np span { color:var(--dim); font-size:12px; }
  .pbtn { flex:none; min-width:38px; height:36px; border-radius:9px;
          border:1px solid var(--line); background:var(--raised);
          color:var(--text); font-size:15px; font-weight:600; display:grid;
          place-items:center; text-decoration:none; padding:0 10px; }
  .pbtn:active { background:var(--hover); }
  .pbtn[disabled] { opacity:.35; }
  #save { border-color:var(--green-dim); color:var(--green);
          background:var(--green-dark); font-size:13px; }
  /* The write row. Absent entirely unless this request may write, so a
     read-only phone looks exactly as it did before writes existed. */
  #edit { display:none; flex-wrap:wrap; gap:7px; align-items:center;
          padding:8px 12px 9px; background:var(--surface);
          border-bottom:1px solid var(--line); }
  #edit.on { display:flex; }
  #rp { display:flex; gap:7px; }
  .ebtn { flex:none; min-width:var(--tap); height:36px; padding:0 15px;
          border-radius:9px; border:1px solid var(--line);
          background:var(--raised); color:var(--dim); font-size:14px;
          font-weight:600; }
  .ebtn:active { background:var(--hover); }
  .ebtn[data-on="1"] { color:var(--text); border-color:var(--line2); }
  .ebtn[data-rp="Run"][data-on="1"] { border-color:var(--run);
      color:var(--run); background:rgba(87,201,138,.15); }
  .ebtn[data-rp="Pass"][data-on="1"] { border-color:var(--pass);
      color:var(--pass); background:rgba(77,163,255,.14); }
  /* RPO and Screen are offered here too, so they get the tag map's
     colours rather than falling back to plain white and losing the
     at-a-glance match with the desktop timeline. */
  .ebtn[data-rp="RPO"][data-on="1"] { border-color:var(--rpo);
      color:var(--rpo); background:rgba(54,214,192,.14); }
  .ebtn[data-rp="Screen"][data-on="1"] { border-color:var(--screen);
      color:var(--screen); background:rgba(149,103,216,.16); }
  #rev[data-on="1"] { border-color:var(--review); color:var(--review);
      background:rgba(241,125,114,.14); }
  /* The play's own timeline. The window is the play plus a margin either
     side, not the whole film, because on a two-hour film a four-second
     rep is a third of a pixel - nothing to see and nothing to hit. */
  #tl { position:relative; height:34px; background:#0a0c0b;
        border-bottom:1px solid var(--line); touch-action:none;
        overflow:hidden; }
  /* Neighbouring plays, so the edge of the previous rep is visible and
     the gap between them reads as a gap. */
  #tlneighbours div { position:absolute; top:9px; height:16px;
                      background:rgba(155,160,150,.16); border-radius:2px; }
  #tlplay { position:absolute; top:6px; height:22px; border-radius:3px;
            background:rgba(57,224,122,.20); }
  .tledge { position:absolute; top:0; width:2px; height:100%;
            background:var(--green); }
  #tlhead { position:absolute; top:0; width:2px; height:100%;
            background:var(--text); box-shadow:0 0 4px rgba(0,0,0,.6); }
  /* The jog. Laid out across rather than down: a dial big enough for a
     thumb plus a stacked readout costs 120px of height side by side and
     twice that stacked, and the play list is what the height is for. */
  #jog { display:none; align-items:center; gap:14px; padding:10px 14px 12px;
         background:var(--surface); border-bottom:1px solid var(--line); }
  #jog.on { display:flex; }
  #dialwrap { flex:none; position:relative; width:130px; height:130px; }
  /* touch-action:none is what stops the page scrolling out from under a
     turning thumb - without it the gesture is unusable on a phone. */
  #dial { width:100%; height:100%; display:block; touch-action:none;
          cursor:grab; }
  #dial.live { cursor:grabbing; }
  #jogplay { position:absolute; left:50%; top:50%; width:52px; height:52px;
             transform:translate(-50%,-50%); border-radius:50%; border:0;
             background:transparent; color:#8fd8ac; font-size:19px;
             line-height:1; padding:0; display:grid; place-items:center; }
  #jogplay:active { color:var(--green-hi); }
  #jogread { flex:1; min-width:0; }
  #jogtc { font-size:17px; font-weight:600; color:#c9e8d5; letter-spacing:.2px;
           font-variant-numeric:tabular-nums; }
  #jog.live #jogtc { color:var(--green); }
  #jogpos { margin-top:2px; font-size:12px; color:var(--faint);
            font-variant-numeric:tabular-nums; }
  #jogjump { display:flex; gap:5px; margin-top:8px; }
  #jogjump button { flex:1; min-width:0; height:29px; padding:0 4px;
                    border-radius:7px; font-size:11px; font-weight:600;
                    font-family:inherit; border:1px solid var(--line2);
                    background:var(--raised); color:var(--dim);
                    white-space:nowrap; }
  #jogjump button:active { background:var(--hover); color:var(--text); }
  #estat { flex-basis:100%; font-size:12px; color:var(--faint); }
  #estat:empty { display:none; }
  #estat.bad { color:var(--review); }
  header { position:sticky; top:0; z-index:10; background:var(--bg);
           border-bottom:1px solid var(--line); }
  .htop { display:flex; align-items:center; gap:9px; padding:12px 14px 0; }
  #mark { flex:none; width:19px; height:19px; display:block; }
  /* Styled as a heading rather than a form control: it is the title of
     the page and also the way to change game, and a native select is the
     one control a phone already knows how to open full-screen. */
  #proj { flex:1; min-width:0; font-size:16px; font-weight:600;
          font-family:inherit; color:var(--text); background:transparent;
          border:0; padding:0 20px 0 0; -webkit-appearance:none;
          appearance:none; text-overflow:ellipsis;
          background-image:linear-gradient(45deg,transparent 50%,var(--dim) 50%),
                           linear-gradient(135deg,var(--dim) 50%,transparent 50%);
          background-position:right 7px center, right 2px center;
          background-size:5px 5px, 5px 5px; background-repeat:no-repeat; }
  #proj:focus { outline:none; }
  #proj option { background:var(--surface); color:var(--text);
                 font-size:15px; font-weight:400; }
  #count { margin-left:auto; color:var(--faint); font-size:12px;
           font-variant-numeric:tabular-nums; white-space:nowrap; }
  .searchwrap { padding:10px 14px 0; position:relative; }
  #q { width:100%; height:var(--tap); padding:0 38px 0 14px; font-size:16px;
       border-radius:11px; border:1px solid var(--line);
       background:var(--surface); color:var(--text); }
  #q:focus { outline:none; border-color:var(--green-dim);
       background:var(--surface); }
  #clear { position:absolute; right:24px; top:52%; transform:translateY(-42%);
           width:26px; height:26px; border-radius:50%; border:0; display:none;
           background:var(--raised); color:var(--dim); font-size:15px; }
  #chips { display:flex; gap:7px; overflow-x:auto; padding:10px 14px 11px;
           scrollbar-width:none; -webkit-overflow-scrolling:touch; }
  #chips::-webkit-scrollbar { display:none; }
  .chip { flex:none; height:32px; padding:0 13px; border-radius:99px;
          transition:background .12s ease, border-color .12s ease,
                     color .12s ease;
          border:1px solid var(--line); background:var(--surface);
          color:var(--dim); font-size:13px; font-weight:600; white-space:nowrap;
          display:flex; align-items:center; gap:6px; }
  .chip i { font-style:normal; font-size:11px; color:var(--faint);
            font-variant-numeric:tabular-nums; }
  .chip[data-on="1"] { background:var(--raised); color:var(--text);
                       border-color:var(--line2); }
  .chip[data-k="run_pass"][data-v="Run"][data-on="1"] { border-color:var(--run); color:var(--run); }
  .chip[data-k="run_pass"][data-v="Pass"][data-on="1"] { border-color:var(--pass); color:var(--pass); }
  .chip[data-k="flag"][data-on="1"] { border-color:var(--review); color:var(--review); }
  ul { list-style:none; margin:0; padding:0 0 40px; }
  /* A list that ends flush with the home indicator looks truncated. */
  ul { padding-bottom:calc(40px + env(safe-area-inset-bottom)); }
  li { display:flex; gap:11px; align-items:flex-start; padding:11px 14px;
       border-bottom:1px solid var(--line); min-height:var(--tap); }
  li { transition:background .12s ease; }
  li:active { background:var(--raised); }
  li[data-playing="1"] { background:rgba(57,224,122,.07);
       box-shadow:inset 3px 0 0 var(--green); }
  .num { flex:none; width:30px; text-align:right; color:var(--faint);
         font-size:12px; font-variant-numeric:tabular-nums; padding-top:2px; }
  .body { flex:1; min-width:0; }
  .line1 { display:flex; align-items:baseline; gap:8px; }
  .sit { font-weight:600; font-size:15px; }
  .dur { margin-left:auto; color:var(--faint); font-size:12px;
         font-variant-numeric:tabular-nums; white-space:nowrap; }
  .line2 { margin-top:4px; display:flex; flex-wrap:wrap; gap:5px; }
  .t { font-size:11px; font-weight:600; padding:2px 8px; border-radius:99px;
       background:var(--raised); color:var(--dim); }
  .t.run { background:rgba(87,201,138,.15); color:var(--run); }
  .t.pass { background:rgba(77,163,255,.14); color:var(--pass); }
  .t.rev { background:rgba(241,125,114,.14); color:var(--review); }
  .empty { padding:52px 24px; text-align:center; color:var(--faint);
           font-size:14px; line-height:1.5; }
  /* Shown before the first fetch lands, so the page is never blank. */
  .skel { padding:14px; }
  .skel div { height:52px; border-radius:10px; margin-bottom:8px;
              background:linear-gradient(90deg,var(--surface) 25%,
                         var(--raised) 37%,var(--surface) 63%);
              background-size:400% 100%; animation:sh 1.3s ease infinite; }
  @keyframes sh { 0% { background-position:100% 0 } 100% { background-position:0 0 } }
  .empty b { display:block; color:var(--dim); margin-bottom:5px; font-size:15px; }
  #warn { background:rgba(241,125,114,.13); color:var(--review); font-size:13px;
          padding:9px 14px; display:none; }
  /* Pending edits live on the phone until they are pushed. The bar is the
     only place that says so, so it stays put rather than auto-hiding. */
  #sync { position:fixed; left:0; right:0;
          bottom:calc(58px + env(safe-area-inset-bottom)); z-index:45;
          display:none; align-items:center; gap:9px; padding:9px 14px;
          background:rgba(57,224,122,.09); border-bottom:1px solid var(--line);
          font-size:13px; }
  #sync.on { display:flex; }
  #syncmsg { flex:1; color:var(--green); }
  #sync button { flex:none; height:30px; padding:0 12px; border-radius:8px;
                 font-size:13px; font-weight:600;
                 border:1px solid var(--green-dim);
                 background:var(--green-dark); color:var(--green); }
  #syncdrop { border-color:var(--line); color:var(--faint); }
  #sync button[disabled] { opacity:.45; }
  .pend { font-size:11px; font-weight:600; padding:2px 8px; border-radius:99px;
          background:rgba(57,224,122,.13); color:var(--green); }
  .who { display:flex; gap:8px; width:100%; margin-top:8px; }
  .who label { flex:1; min-width:0; display:flex; flex-direction:column;
               gap:4px; font-size:11px; font-weight:600; color:var(--faint);
               text-transform:uppercase; letter-spacing:.4px; }
  .who input { width:100%; height:38px; padding:0 11px; font-size:16px;
               border-radius:9px; border:1px solid var(--line);
               background:var(--bg); color:var(--text); font-family:inherit;
               font-weight:400; text-transform:none; letter-spacing:0; }
  .who input:focus { outline:none; border-color:var(--green-dim); }
</style>
</head>
<body>
<div id="sheet">
  <div id="grab"><span></span><button id="sheetclose" aria-label="Close">&times;</button></div>
  <div id="player">
  <video id="v" playsinline controls preload="metadata"></video>
  <!-- The native scrubber spans the whole film, where a play is a sliver
       too small to see or hit. This one spans the play plus a margin
       either side, so the boundaries are visible and reachable. -->
  <div id="tl">
    <div id="tlneighbours"></div>
    <div id="tlplay"></div>
    <div id="tlin" class="tledge"></div>
    <div id="tlout" class="tledge"></div>
    <div id="tlhead"></div>
  </div>
  <div id="bar">
    <button class="pbtn" id="prev">&#8249;</button>
    <div id="np"></div>
    <button class="pbtn" id="next">&#8250;</button>
    <a class="pbtn" id="save" download>Save</a>
  </div>
  <div id="edit">
    <div id="rp"></div>
    <button class="ebtn" id="rev">Review</button>
    <span id="estat"></span>
    <div class="who">
      <label>Key guy<input id="key" list="names" placeholder="name or number"
             autocomplete="off" autocorrect="off" enterkeyhint="done"></label>
      <label>Involved<input id="inv" list="names"
             placeholder="comma separated" autocomplete="off"
             autocorrect="off" enterkeyhint="done"></label>
    </div>
    <datalist id="names"></datalist>
  </div>
  <!-- Its own block rather than part of #edit: scrubbing is not editing,
       so a read-only phone gets the dial too. It sits last so it lands
       under the player, the tags and the name fields. -->
  <div id="jog">
    <div id="jogread">
      <div id="jogtc">00:00:00:00</div>
      <div id="jogpos"></div>
      <div id="jogjump">
        <button id="jogstart">IN / Play start</button>
        <button id="jogend">OUT / Play end</button>
      </div>
    </div>
    <div id="dialwrap">
      <svg id="dial" viewBox="0 0 116 116" aria-hidden="true">
        <defs>
          <radialGradient id="dish" cx="38%" cy="30%" r="78%">
            <stop offset="0" stop-color="#24261f"/>
            <stop offset=".72" stop-color="#1c1e17"/>
            <stop offset="1" stop-color="#131510"/>
          </radialGradient>
          <radialGradient id="well" cx="50%" cy="34%" r="72%">
            <stop offset="0" stop-color="#1e2a21"/>
            <stop offset="1" stop-color="#0b0f0a"/>
          </radialGradient>
        </defs>
        <circle cx="58" cy="58" r="54" fill="url(#dish)" stroke="#3e4038"/>
        <g id="ticks"></g>
        <path id="jarc" fill="none" stroke="#2f6b47" stroke-width="2.4"
              stroke-linecap="round"/>
        <circle id="jmark" r="3.2" fill="#55d185" cx="58" cy="18"/>
        <circle cx="58" cy="58" r="25" fill="url(#well)" stroke="#35a468"
                stroke-width="2.2"/>
      </svg>
      <button id="jogplay" aria-label="Play or pause">&#9654;</button>
    </div>
  </div>
  </div>
</div>

<section class="screen on" id="sc-plays">
  <header>
    <div class="htop"><img id="mark" src="icon.svg" alt=""><b id="gamename"></b><span id="count"></span></div>
    <div class="searchwrap">
      <input id="q" placeholder="Search plays" autocomplete="off"
             autocorrect="off" autocapitalize="none" enterkeyhint="search">
      <button id="clear">&times;</button>
    </div>
    <div id="chips"></div>
  </header>
  <div id="warn"></div>
  <ul id="list"></ul>
  <div class="skel" id="skel"><div></div><div></div><div></div><div></div><div></div></div>
</section>

<section class="screen" id="sc-export">
  <div class="shead"><b>Export</b><span id="xcount"></span></div>
  <div class="xbar">
    <button id="xall">Select all</button>
    <button id="xnone">Clear</button>
    <button id="xreview">Needs review</button>
  </div>
  <ul id="xlist"></ul>
  <div id="xfoot">
    <div id="xmsg">Nothing selected</div>
    <button id="xgo" class="xbtn">Cut reel</button>
  </div>
</section>

<section class="screen" id="sc-games">
  <div class="shead"><b>Games</b><span id="gcount"></span></div>
  <select id="proj"></select>
  <ul id="glist"></ul>
</section>

<div id="sync">
  <span id="syncmsg"></span>
  <button id="syncgo">Sync</button>
  <button id="syncdrop">Discard</button>
</div>

<nav id="tabs">
  <button class="tab on" data-s="plays"><i></i><span>Plays</span></button>
  <button class="tab" data-s="export"><i></i><span>Export</span></button>
  <button class="tab" data-s="games"><i></i><span>Games</span></button>
</nav>
<script>
let PLAYS = [], SHOWN = [], CURRENT = null;
let CAN_EDIT = false, RP_VALUES = [], REVIEW = 'needs-review';

/* Edits are kept on the phone and pushed only when asked.

   This is not just convenience. The desktop app holds every clip in
   memory from the moment it opens a project, and its save is a
   replace-all - so anything written to the file while it is open is
   erased by the next desktop save without a word. Queueing here means a
   game can be tagged on a sideline with no network and no desktop, and
   the write happens later at a moment when it is known to be safe.

   localStorage survives a reload, a locked screen, and Safari discarding
   the tab, which is the difference between a queue and a good intention. */
const PENDKEY = 'tapesift.pending';
let PENDING = {};
try { PENDING = JSON.parse(localStorage.getItem(PENDKEY) || '{}'); }
catch (e) { PENDING = {}; }

const pendId = (project, number) => project + '\u0000' + number;

function savePending() {
  try { localStorage.setItem(PENDKEY, JSON.stringify(PENDING)); }
  catch (e) { status('Phone storage full - sync now', true); }
}

function pendingFor(project) {
  return Object.keys(PENDING).filter(k => k.indexOf(project + '\u0000') === 0);
}

function paintSync() {
  const mine = pendingFor(PROJECT);
  const bar = $('sync');
  bar.className = mine.length ? 'on' : '';
  if (!mine.length) return;
  const n = mine.length;
  $('syncmsg').textContent =
    n + (n === 1 ? ' edit' : ' edits') + ' saved on this phone';
}
const FILTERS = {};
const $ = id => document.getElementById(id);
const v = $('v');
// The write token rides in the URL the server printed. Opening the same
// address without it gives the read-only page, which is the point.
const TOKEN = new URLSearchParams(location.search).get('t') || '';
const AUTH = TOKEN ? { 'X-TapeSift-Token': TOKEN } : {};

let PROJECT = '';

// Every request that touches a project has to name it, or a phone that
// switched games streams the previous one's film and writes tags onto
// the wrong game.
const withProject = url =>
  PROJECT ? url + (url.indexOf('?') < 0 ? '?' : '&') +
            'project=' + encodeURIComponent(PROJECT) : url;

// Rebuilt in place rather than wholesale, so the background refresh can
// pick up a game added on the desktop without collapsing an open picker
// or losing the selection under a thumb.
function fillProjects(list) {
  const sel = $('proj');
  // JSON rather than a joined string with a separator character: a
  // separator has to be one that cannot appear in a game name, and that
  // is a needless thing to have to be right about.
  const want = JSON.stringify((list || []).map(p => [p.id, p.name, p.plays]));
  if (sel.dataset.shape === want) return true;
  sel.dataset.shape = want;
  const keep = sel.value;
  sel.innerHTML = '';
  (list || []).forEach(p => {
    const o = document.createElement('option');
    o.value = p.id;
    o.textContent = p.name + '  (' + p.plays + ')';
    sel.appendChild(o);
  });
  if (keep && [].slice.call(sel.options).some(o => o.value === keep)) sel.value = keep;
  return !!sel.options.length;
}

fetch('api/projects', { headers: AUTH }).then(r => r.json()).then(d => {
  const sel = $('proj');
  if (!fillProjects(d.projects)) {
    $('warn').style.display = 'block';
    $('warn').textContent = 'No projects found in that folder.';
    return;
  }
  // Remember the last game across reloads, so picking the phone up again
  // on a sideline does not land on whichever was edited most recently.
  const saved = localStorage.getItem('tapesift.project');
  if (saved && [].slice.call(sel.options).some(o => o.value === saved)) {
    sel.value = saved;
  }
  PROJECT = sel.value;
  sel.onchange = () => {
    PROJECT = sel.value;
    localStorage.setItem('tapesift.project', PROJECT);
    // A different game means the loaded film and the playing clip are
    // both wrong. Drop them rather than leaving the last game's video
    // under the new game's list.
    CURRENT = null;
    v.pause();
    v.removeAttribute('src');
    v.load();
    closeSheet();
    loadPlays();
  };
  loadPlays();
});

function loadPlays(quiet) {
  if (!quiet) $('warn').style.display = 'none';
  const wasOn = CURRENT ? CURRENT.number : null;
  const scrollWas = window.scrollY;
  fetch(withProject('api/plays'), { headers: AUTH })
    .then(r => r.json()).then(d => {
      PLAYS = d.plays.map((p, i) => ({ ...p, i }));
      // The server decides this, not the presence of a token: it answers
      // "may this request write", so a stale or wrong token shows
      // read-only rather than buttons that fail on tap.
      CAN_EDIT = !!d.writable;
      RP_VALUES = d.run_pass_values || [];
      REVIEW = d.review_tag || REVIEW;
      if (!d.film_available) {
        $('warn').style.display = 'block';
        $('warn').textContent =
          'Source film not found on the desktop - list only.';
      }
      // A link carrying a token the server will not accept is the one
      // failure that looks like nothing at all: the page loads, the list
      // is right, and only a Sync tap much later says otherwise. Say it
      // on arrival, and say that queued work is not lost.
      if (TOKEN && !CAN_EDIT) {
        const held = pendingFor(PROJECT).length;
        $('warn').style.display = 'block';
        $('warn').textContent =
          'This link is out of date, so tagging is off. Open the current '
          + 'URL to turn it back on.'
          + (held ? ' Your ' + held + ' saved edit' + (held === 1 ? '' : 's')
                    + ' are safe on this phone until then.' : '');
      }
      // Anything queued on this phone is re-applied over the file's
      // version, so a reload shows what was tagged rather than losing it
      // behind whatever the project last said.
      pendingFor(PROJECT).forEach(k => {
        const item = PENDING[k];
        const play = PLAYS.filter(x => x.number === item.number)[0];
        if (!play) return;
        if ('run_pass' in item.patch) {
          play.details.run_pass = item.patch.run_pass;
        }
        if ('player_name' in item.patch) {
          const pn = String(item.patch.player_name || '').trim();
          if (pn) play.details.player_name = pn;
          else delete play.details.player_name;
        }
        if ('other_players' in item.patch) {
          const op = String(item.patch.other_players || '').trim();
          if (op) play.details.other_players = op;
          else delete play.details.other_players;
        }
        if ('needs_review' in item.patch) {
          const has = play.tags.indexOf(REVIEW) >= 0;
          if (item.patch.needs_review && !has) play.tags.push(REVIEW);
          if (!item.patch.needs_review && has) {
            play.tags.splice(play.tags.indexOf(REVIEW), 1);
          }
        }
      });
      // A quiet refresh keeps the chips you set, where you were scrolled
      // and which play is loaded. Wiping those every fifteen seconds
      // would make the page unusable while it was doing its job.
      if (!quiet) Object.keys(FILTERS).forEach(k => delete FILTERS[k]);
      buildEdit();
      loadNames();
      buildChips();
      if (quiet && wasOn !== null) {
        // CURRENT pointed into the old array; re-point it at the same
        // play in the new one so edits and the jog keep working.
        const again = PLAYS.filter(x => x.number === wasOn)[0];
        if (again) { CURRENT = again; paintNow(); paintEdit(); paintTimeline(); }
      }
      render();
      paintSync();
      if (SCREEN === 'export') buildExport();
      if (SCREEN === 'games') buildGames();
      if (!quiet) window.scrollTo({ top: 0 });
      else window.scrollTo(0, scrollWas);
    });
}

// ---- keeping up to date ------------------------------------------------
// The desktop is the thing logging film; a phone left open on a sideline
// should not be showing what the project said twenty minutes ago. Pulled
// rather than pushed because there is no socket here and this costs one
// small request a quarter of a minute.
const REFRESH_MS = 15000;

// Never mid-gesture, mid-sync or mid-typing: a refresh redraws the list
// and repoints CURRENT, and doing that under a moving thumb is how a tag
// lands on the wrong play.
function refreshBusy() {
  const a = document.activeElement;
  return jId !== null || tlId !== null || $('syncgo').disabled ||
         (a && (a.tagName === 'INPUT' || a.tagName === 'SELECT'));
}

setInterval(() => {
  if (document.hidden || !PROJECT || refreshBusy()) return;
  fetch('api/projects', { headers: AUTH })
    .then(r => r.json()).then(d => fillProjects(d.projects))
    .catch(() => {});
  loadPlays(true);
}, REFRESH_MS);

// Coming back to the phone after it slept should not wait out the timer.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && PROJECT && !refreshBusy()) loadPlays(true);
});

// A play's value for one facet. Down is parsed out of "3rd & 8".
function facet(p, key) {
  if (key === 'down') {
    const m = (p.details.down_distance || '').match(/^(1st|2nd|3rd|4th)/i);
    return m ? m[1].toLowerCase() : '';
  }
  return p.details[key] || '';
}

// Chips are built from the data, so a project that never logged a quarter
// does not get a row of empty quarter chips.
function buildChips() {
  const box = $('chips');
  const count = (k, val) => PLAYS.filter(p => facet(p, k) === val).length;
  const rev = PLAYS.filter(p => p.tags.includes(REVIEW)).length;
  const wanted = [];
  ['Run', 'Pass'].forEach(x => count('run_pass', x) && wanted.push(['run_pass', x, count('run_pass', x)]));
  ['Q1','Q2','Q3','Q4','OT'].forEach(x => count('quarter', x) && wanted.push(['quarter', x, count('quarter', x)]));
  ['1st','2nd','3rd','4th'].forEach(x => count('down', x) && wanted.push(['down', x, count('down', x)]));
  if (rev) wanted.push(['flag', 'Review', rev]);

  box.innerHTML = '';
  wanted.forEach(([k, val, n]) => {
    const c = document.createElement('button');
    c.className = 'chip'; c.dataset.k = k; c.dataset.v = val;
    // Read the on-state back from FILTERS rather than defaulting to off:
    // an edit rebuilds this row to fix the counts, and a chip that looked
    // off while still filtering would be a list with plays missing and
    // nothing on screen saying why.
    c.dataset.on = (FILTERS[k] && FILTERS[k].has(val)) ? '1' : '0';
    c.innerHTML = val + ' <i>' + n + '</i>';
    c.onclick = () => {
      const on = c.dataset.on === '1';
      c.dataset.on = on ? '0' : '1';
      FILTERS[k] = FILTERS[k] || new Set();
      if (on) FILTERS[k].delete(val); else FILTERS[k].add(val);
      render();
    };
    box.appendChild(c);
  });
}

function passesFilters(p) {
  for (const k of Object.keys(FILTERS)) {
    const set = FILTERS[k];
    if (!set || !set.size) continue;
    if (k === 'flag') {
      if (!p.tags.includes(REVIEW)) return false;
      continue;
    }
    // Within one facet chips are OR, across facets they are AND: Q1 or Q2,
    // and Pass. That is how two rows of chips read to anyone.
    if (!set.has(facet(p, k))) return false;
  }
  return true;
}

function render() {
  const terms = $('q').value.toLowerCase().split(/\s+/).filter(Boolean);
  $('clear').style.display = terms.length ? 'block' : 'none';
  SHOWN = PLAYS.filter(p => {
    if (!passesFilters(p)) return false;
    if (!terms.length) return true;
    const hay = (p.title + ' ' + p.tags.join(' ') + ' ' +
                 Object.values(p.details).join(' ')).toLowerCase();
    return terms.every(t => hay.includes(t));
  });
  $('count').textContent = SHOWN.length === PLAYS.length
    ? PLAYS.length + ' plays' : SHOWN.length + ' of ' + PLAYS.length;
  const sel = $('proj').selectedOptions[0];
  if (sel) {
    $('gamename').textContent =
      sel.textContent.replace(/\s+\(\d+\)\s*$/, '').trim();
  }

  const skel = $('skel');
  if (skel) skel.remove();
  const list = $('list');
  if (!SHOWN.length) {
    list.innerHTML = '<div class="empty"><b>Nothing matches</b>' +
      'Try fewer filters, or a shorter search.</div>';
    return;
  }
  list.innerHTML = '';
  SHOWN.forEach(p => {
    const rp = (p.details.run_pass || '').toLowerCase();
    const sit = [p.details.quarter, p.details.down_distance]
                  .filter(Boolean).join('  ') || p.title;
    const extra = p.tags.filter(t =>
        [REVIEW, 'Run', 'Pass'].indexOf(t) < 0 &&
        t !== p.details.down_distance).slice(0, 3);
    const li = document.createElement('li');
    if (CURRENT && CURRENT.i === p.i) li.dataset.playing = '1';
    li.innerHTML =
      '<div class="num">' + p.number + '</div><div class="body">' +
      '<div class="line1"><span class="sit">' + esc(sit) + '</span>' +
      '<span class="dur">' + fmt(p.start_ms) + ' &middot; ' + p.duration_s + 's</span></div>' +
      '<div class="line2">' +
      (rp ? '<span class="t ' + rp + '">' + esc(p.details.run_pass) + '</span>' : '') +
      (p.tags.indexOf(REVIEW) >= 0 ? '<span class="t rev">Review</span>' : '') +
      extra.map(t => '<span class="t">' + esc(t) + '</span>').join('') +
      '</div></div>';
    li.onclick = () => play(p);
    list.appendChild(li);
  });
}

// The header line, redrawn on its own because an edit changes what it
// says (it carries run_pass) without changing what is playing.
function paintNow() {
  const p = CURRENT;
  if (!p) return;
  const sit = [p.details.quarter, p.details.down_distance,
               p.details.run_pass].filter(Boolean).join('  ');
  $('np').innerHTML = '<b>' + esc(sit || p.title) + '</b><span>Play ' +
                      p.number + ' &middot; ' + fmt(p.start_ms) + '</span>';
  const at = SHOWN.findIndex(x => x.i === p.i);
  $('prev').disabled = at <= 0;
  $('next').disabled = at < 0 || at >= SHOWN.length - 1;
}

function play(p) {
  CURRENT = p;
  openSheet();
  $('jog').className = 'on';
  paintTimeline();
  paintNow();
  paintEdit();
  $('save').href = withProject('clip/' + p.number + '.mp4');
  if (!v.src) v.src = withProject('film');
  v.dataset.stop = p.end_ms / 1000;
  const go = () => { v.currentTime = p.start_ms / 1000; v.play(); };
  // Seeking before metadata loads is silently ignored, which looks like
  // the wrong play starting from the top of the film.
  if (v.readyState >= 1) go();
  else v.addEventListener('loadedmetadata', go, { once: true });
  render();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function step(delta) {
  const at = SHOWN.findIndex(x => CURRENT && x.i === CURRENT.i);
  const next = SHOWN[at + delta];
  if (next) play(next);
}

// ---- the shell ---------------------------------------------------------
// Three screens and a sheet. The list owns the viewport; a play raises the
// sheet over it. Before this the player sat above the list permanently and
// took ~630px of a 720px phone, which left the list with nothing.

let SCREEN = 'plays';

function showScreen(name) {
  SCREEN = name;
  ['plays', 'export', 'games'].forEach(n => {
    $('sc-' + n).className = 'screen' + (n === name ? ' on' : '');
  });
  Array.prototype.forEach.call($('tabs').children, t => {
    t.className = 'tab' + (t.dataset.s === name ? ' on' : '');
  });
  if (name === 'export') buildExport();
  if (name === 'games') buildGames();
  window.scrollTo(0, 0);
}
Array.prototype.forEach.call($('tabs').children, t => {
  t.onclick = () => showScreen(t.dataset.s);
});

function openSheet() {
  $('sheet').className = 'on';
  document.body.style.overflow = 'hidden';
}
function closeSheet() {
  $('sheet').className = '';
  document.body.style.overflow = '';
  v.pause();
}
$('sheetclose').onclick = closeSheet;
// Swiping the grab bar down is the gesture people try first.
let grabY = null;
$('grab').addEventListener('pointerdown', e => { grabY = e.clientY; });
$('grab').addEventListener('pointerup', e => {
  if (grabY !== null && e.clientY - grabY > 40) closeSheet();
  grabY = null;
});

// ---- export ------------------------------------------------------------
// One reel out of the plays you tick, rather than saving them one at a
// time. A browser cannot be handed forty downloads and a person cannot
// share forty files.
const PICKED = new Set();

function buildExport() {
  const box = $('xlist');
  box.innerHTML = '';
  PLAYS.forEach(p => {
    const li = document.createElement('li');
    li.dataset.on = PICKED.has(p.number) ? '1' : '0';
    const sit = [p.details.quarter, p.details.down_distance]
                  .filter(Boolean).join('  ') || p.title;
    li.innerHTML = '<span class="box"></span><span class="xt">' +
      p.number + ' \u00b7 ' + esc(sit) + '</span>' +
      '<span class="xd">' + p.duration_s + 's</span>';
    li.onclick = () => {
      if (PICKED.has(p.number)) PICKED.delete(p.number);
      else PICKED.add(p.number);
      li.dataset.on = PICKED.has(p.number) ? '1' : '0';
      clearReel();
      paintExport();
    };
    box.appendChild(li);
  });
  paintExport();
}

function paintExport() {
  const nums = PLAYS.filter(p => PICKED.has(p.number)).map(p => p.number);
  const secs = PLAYS.filter(p => PICKED.has(p.number))
                    .reduce((a, p) => a + p.duration_s, 0);
  $('xcount').textContent = PLAYS.length + ' plays';
  const go = $('xgo');
  if (REEL) return;
  if (!nums.length) {
    $('xmsg').textContent = 'Nothing selected';
    go.setAttribute('aria-disabled', 'true');
  } else {
    $('xmsg').textContent = nums.length + (nums.length === 1 ? ' play' : ' plays')
      + ' \u00b7 ' + Math.round(secs) + 's';
    go.removeAttribute('aria-disabled');
  }
}
// Two taps, and the page never navigates.
//
// Navigating to the reel was the trap: in the same tab the share sheet
// replaced the app, and in a new tab there was no history to go back
// through - and added to the homescreen there is no browser UI at all, so
// either way the way out was to kill the app. Nothing here leaves the page.
//
// It is two taps because the share sheet has to be opened from inside the
// tap that asked for it. Cutting takes seconds, which spends that
// permission, so the cut happens on the first tap and the sheet opens on
// the second - by then the file is already in hand and the sheet is
// instant.
let REEL = null;

function reelName(response, fallback) {
  const raw = response.headers.get('content-disposition') || '';
  const hit = /filename="([^"]+)"/.exec(raw);
  return hit ? hit[1] : fallback;
}

function clearReel() {
  REEL = null;
  $('xgo').textContent = 'Cut reel';
  $('xgo').removeAttribute('data-ready');
}

async function cutReel() {
  const nums = PLAYS.filter(p => PICKED.has(p.number)).map(p => p.number);
  if (!nums.length) return;
  const go = $('xgo');
  go.disabled = true;
  $('xmsg').textContent = 'Cutting ' + nums.length
    + (nums.length === 1 ? ' play' : ' plays') + '\u2026';
  try {
    const r = await fetch(withProject('reel') + '&plays=' + nums.join(','),
                          { headers: AUTH });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const total = +(r.headers.get('content-length') || 0);
    const parts = [];
    let got = 0;
    // Read it in pieces so a big reel shows movement instead of a stall.
    const reader = r.body.getReader();
    for (;;) {
      const step = await reader.read();
      if (step.done) break;
      parts.push(step.value);
      got += step.value.length;
      $('xmsg').textContent = total
        ? 'Cutting\u2026 ' + Math.round((got / total) * 100) + '%'
        : 'Cutting\u2026 ' + Math.round(got / 1048576) + ' MB';
    }
    const name = reelName(r, 'reel.mp4');
    REEL = { blob: new Blob(parts, { type: 'video/mp4' }), name: name };
    go.textContent = 'Save';
    go.dataset.ready = '1';
    $('xmsg').textContent = name + ' \u00b7 '
      + (REEL.blob.size / 1048576).toFixed(1) + ' MB \u2014 tap Save';
  } catch (err) {
    clearReel();
    $('xmsg').textContent = 'Could not cut: ' + (err.message || err);
  } finally {
    go.disabled = false;
  }
}

async function saveReel() {
  const file = new File([REEL.blob], REEL.name, { type: 'video/mp4' });
  if (navigator.canShare && navigator.canShare({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title: REEL.name });
      $('xmsg').textContent = 'Sent \u2014 still ' + PICKED.size + ' selected';
    } catch (err) {
      // Dismissing the sheet is not a failure worth shouting about.
      $('xmsg').textContent = err && err.name === 'AbortError'
        ? 'Cancelled \u2014 tap Save to try again'
        : 'Could not share: ' + (err.message || err);
    }
    return;
  }
  // No share sheet here: fall back to an ordinary download, which on a
  // desktop browser lands in the downloads folder without navigating.
  const href = URL.createObjectURL(REEL.blob);
  const a = document.createElement('a');
  a.href = href;
  a.download = REEL.name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(href), 30000);
  $('xmsg').textContent = 'Saved ' + REEL.name;
}

$('xgo').addEventListener('click', () => {
  if ($('xgo').getAttribute('aria-disabled') === 'true') return;
  if (REEL) saveReel(); else cutReel();
});

$('xall').onclick = () => { SHOWN.forEach(p => PICKED.add(p.number)); clearReel(); buildExport(); };
$('xnone').onclick = () => { PICKED.clear(); clearReel(); buildExport(); };
$('xreview').onclick = () => {
  PLAYS.forEach(p => { if (p.tags.indexOf(REVIEW) >= 0) PICKED.add(p.number); });
  clearReel();
  buildExport();
};

// ---- games -------------------------------------------------------------
function buildGames() {
  const box = $('glist');
  box.innerHTML = '';
  const sel = $('proj');
  $('gcount').textContent = sel.options.length + ' games';
  Array.prototype.forEach.call(sel.options, o => {
    const li = document.createElement('li');
    li.dataset.on = o.value === PROJECT ? '1' : '0';
    const m = o.textContent.match(/^(.*)\s+\((\d+)\)\s*$/);
    const name = m ? m[1].trim() : o.textContent.trim();
    const n = m ? +m[2] : 0;
    li.innerHTML = '<span class="gn">' + esc(name) + '</span>' +
      (n ? '<span class="gc">' + n + '</span>'
         : '<span class="gz">no plays</span>');
    li.onclick = () => {
      if (o.value === PROJECT) { showScreen('plays'); return; }
      sel.value = o.value;
      sel.onchange();
      showScreen('plays');
    };
    box.appendChild(li);
  });
}

// ---- seeking -----------------------------------------------------------
// Setting currentTime on every pointermove is dozens of seeks a second.
// Chrome on Android queues them and paints only when it catches up, so the
// picture sits still until the finger stops - the frame you are scrubbing
// for is the one you cannot see. So: one seek in flight at a time, and the
// most recent position is remembered and applied when it lands.
//
// Everything reads SEEK_TO rather than v.currentTime while a seek is
// pending. The video element lags behind the finger by a frame or two; the
// dial, the timecode and the timeline should not.
let SEEK_TO = null, seekBusy = false, seekAt = 0;

const targetTime = () => (SEEK_TO !== null ? SEEK_TO : v.currentTime);

function seekTo(t) {
  if (!v.duration) return;
  SEEK_TO = Math.max(0, Math.min(v.duration - 0.02, t));
  // A seek that never reports back would wedge this permanently, so a
  // stalled one is allowed to be overtaken after a second.
  if (!seekBusy || Date.now() - seekAt > 1000) flushSeek();
}

function flushSeek() {
  if (SEEK_TO === null) return;
  const t = SEEK_TO;
  SEEK_TO = null;
  seekBusy = true;
  seekAt = Date.now();
  v.currentTime = t;
}

v.addEventListener('seeked', () => { seekBusy = false; flushSeek(); });

// iOS raises its own control overlay - skip buttons and all - the moment
// the video is touched, and it sits on top of the frame being scrubbed
// for. They come back the moment the gesture ends.
function hideNativeControls() { v.controls = false; }
function showNativeControls() { v.controls = true; flushSeek(); }

// ---- the jog dial ------------------------------------------------------
// The same control the desktop has (ui_core/minimal_jog_ring.py), and
// the same angle maths: the shortest signed delta between where the
// finger was and where it is, accumulated. Turn it either way and the
// film follows.
//
// It runs off pointermove alone - no animation loop. A loop was how the
// first attempt at this worked and it is the part that did not survive
// contact with a real phone; a control that only moves while a thumb is
// moving cannot stall.
const FPS = 30;                 // no way to read the film's real rate here
const DEG_PER_SEC = 90;         // one full turn ~ 4s of film
const LIT_SPREAD = 30, ARC_SPAN = 42, TICK_STEP = 6, MAJOR_EVERY = 5;
const dial = $('dial'), ticks = $('ticks');
let jAngle = -90, jLast = 0, jId = null;

const pol = (deg, r) => [58 + Math.cos(deg * Math.PI / 180) * r,
                         58 + Math.sin(deg * Math.PI / 180) * r];

(function buildTicks() {
  for (let a = 0; a < 360; a += TICK_STEP) {
    const major = (a / TICK_STEP) % MAJOR_EVERY === 0;
    const [x1, y1] = pol(a, 50);
    const [x2, y2] = pol(a, major ? 43.6 : 46.2);
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', x1.toFixed(2)); line.setAttribute('y1', y1.toFixed(2));
    line.setAttribute('x2', x2.toFixed(2)); line.setAttribute('y2', y2.toFixed(2));
    line.setAttribute('stroke-width', major ? '1.6' : '1');
    line.dataset.a = a;
    ticks.appendChild(line);
  }
})();

// Ticks near the marker light up, which is what makes a turn legible at
// all - without it the dial looks identical at every angle.
function paintDial() {
  const [mx, my] = pol(jAngle, 40);
  $('jmark').setAttribute('cx', mx.toFixed(2));
  $('jmark').setAttribute('cy', my.toFixed(2));
  const [ax, ay] = pol(jAngle - ARC_SPAN / 2, 40);
  const [bx, by] = pol(jAngle + ARC_SPAN / 2, 40);
  $('jarc').setAttribute('d',
    'M' + ax.toFixed(2) + ' ' + ay.toFixed(2) +
    'A40 40 0 0 1 ' + bx.toFixed(2) + ' ' + by.toFixed(2));
  Array.prototype.forEach.call(ticks.children, t => {
    const d = Math.abs(((+t.dataset.a - jAngle + 540) % 360) - 180);
    t.setAttribute('stroke', d < LIT_SPREAD ? '#4ecb7b'
      : (t.getAttribute('stroke-width') === '1.6' ? '#6b6f61' : '#4a4d42'));
  });
  paintReadout();
}

const tcode = sec => {
  const t = Math.max(0, sec || 0);
  const parts = [Math.floor(t / 3600), Math.floor(t / 60) % 60,
                 Math.floor(t) % 60, Math.floor((t % 1) * FPS)];
  return parts.map(n => String(n).padStart(2, '0')).join(':');
};

function paintReadout() {
  const now = targetTime();
  $('jogtc').textContent = tcode(now);
  if (!CURRENT) { $('jogpos').textContent = ''; return; }
  const into = now - CURRENT.start_ms / 1000;
  const len = (CURRENT.end_ms - CURRENT.start_ms) / 1000;
  // Outside the play is worth saying plainly - it is the normal way to
  // see the snap before it or what happened after.
  $('jogpos').textContent = into < 0 ? Math.abs(into).toFixed(1) + 's before the play'
    : into > len ? (into - len).toFixed(1) + 's after the play'
    : into.toFixed(1) + 's of ' + len.toFixed(1) + 's';
}

function jAngleAt(e) {
  const b = dial.getBoundingClientRect();
  return Math.atan2(e.clientY - (b.top + b.height / 2),
                    e.clientX - (b.left + b.width / 2)) * 180 / Math.PI;
}

dial.addEventListener('pointerdown', e => {
  if (jId !== null) return;
  jId = e.pointerId;
  jLast = jAngleAt(e);
  dial.classList.add('live');
  $('jog').classList.add('live');
  v.pause();
  hideNativeControls();
  // Taking the film by hand: the auto-stop at the play's end would
  // otherwise fight the dial.
  v.dataset.stop = '';
  try { dial.setPointerCapture(e.pointerId); } catch (err) {}
  e.preventDefault();
});

dial.addEventListener('pointermove', e => {
  if (e.pointerId !== jId) return;
  const now = jAngleAt(e);
  // Shortest signed way round, so crossing 12 o'clock is not a full turn
  // backwards.
  const delta = ((now - jLast + 540) % 360) - 180;
  jLast = now;
  jAngle = (jAngle + delta + 360) % 360;
  // Against targetTime(), not v.currentTime: with a seek still in flight
  // the element reports where it was, so accumulating there would quietly
  // drop most of the turn.
  seekTo(targetTime() + delta / DEG_PER_SEC);
  paintDial();
  paintTimeline();
  e.preventDefault();
});

function jEnd(e) {
  if (e.pointerId !== jId) return;
  jId = null;
  dial.classList.remove('live');
  $('jog').classList.remove('live');
  showNativeControls();
}
dial.addEventListener('pointerup', jEnd);
dial.addEventListener('pointercancel', jEnd);

$('jogplay').onclick = () => {
  if (v.paused) { v.dataset.stop = ''; v.play(); } else v.pause();
};
const paintPlayGlyph = () => {
  $('jogplay').innerHTML = v.paused ? '&#9654;' : '&#10073;&#10073;';
};
v.addEventListener('play', paintPlayGlyph);
v.addEventListener('pause', paintPlayGlyph);
v.addEventListener('seeked', () => { paintReadout(); paintTimeline(); });

$('jogstart').onclick = () => {
  if (!CURRENT || !v.duration) return;
  seekTo(CURRENT.start_ms / 1000);
  // Landing on the start means you are about to watch it, so the stop at
  // the far end goes back on - jogging had cleared it.
  v.dataset.stop = CURRENT.end_ms / 1000;
  paintReadout(); paintTimeline();
};

$('jogend').onclick = () => {
  if (!CURRENT || !v.duration) return;
  // No stop here: you are parked on the result, and arming a stop you are
  // already past would pause the moment anything played.
  seekTo(CURRENT.end_ms / 1000);
  v.dataset.stop = '';
  paintReadout(); paintTimeline();
};

// ---- the play's timeline -----------------------------------------------
// A window around the current play rather than the whole film. The window
// grows to keep the playhead in view, so jogging away from the play makes
// the green block shrink towards the edge instead of the head vanishing.
const tl = $('tl');

function tlWindow() {
  const s = CURRENT.start_ms / 1000, e = CURRENT.end_ms / 1000;
  const pad = Math.max(3, e - s);
  let a = Math.max(0, s - pad), b = Math.min(v.duration, e + pad);
  const at = targetTime();
  if (at < a) a = Math.max(0, at - 0.5);
  if (at > b) b = Math.min(v.duration, at + 0.5);
  return [a, Math.max(a + 0.1, b)];
}

function paintTimeline() {
  if (!CURRENT || !v.duration) { tl.style.display = 'none'; return; }
  tl.style.display = 'block';
  const [a, b] = tlWindow();
  const span = b - a;
  const pct = t => Math.max(0, Math.min(100, ((t - a) / span) * 100));
  const s = CURRENT.start_ms / 1000, e = CURRENT.end_ms / 1000;

  $('tlplay').style.left = pct(s) + '%';
  $('tlplay').style.width = Math.max(0.4, pct(e) - pct(s)) + '%';
  $('tlin').style.left = pct(s) + '%';
  $('tlout').style.left = 'calc(' + pct(e) + '% - 2px)';
  $('tlhead').style.left = 'calc(' + pct(targetTime()) + '% - 1px)';

  const box = $('tlneighbours');
  box.innerHTML = '';
  PLAYS.forEach(p => {
    if (p.i === CURRENT.i) return;
    const ps = p.start_ms / 1000, pe = p.end_ms / 1000;
    if (pe < a || ps > b) return;
    const d = document.createElement('div');
    d.style.left = pct(ps) + '%';
    d.style.width = Math.max(0.4, pct(pe) - pct(ps)) + '%';
    box.appendChild(d);
  });
}

// Dragging it seeks, scoped to the window - which is the scrubber the
// native bar cannot be, since there a whole play is a third of a pixel.
let tlId = null;
function tlSeek(e) {
  const box = tl.getBoundingClientRect();
  const [a, b] = tlWindow();
  const at = a + ((e.clientX - box.left) / box.width) * (b - a);
  seekTo(at);
  v.dataset.stop = '';
  paintReadout(); paintTimeline();
}
tl.addEventListener('pointerdown', e => {
  if (tlId !== null || !CURRENT || !v.duration) return;
  tlId = e.pointerId;
  v.pause();
  hideNativeControls();
  try { tl.setPointerCapture(e.pointerId); } catch (err) {}
  tlSeek(e);
  e.preventDefault();
});
tl.addEventListener('pointermove', e => {
  if (e.pointerId !== tlId) return;
  tlSeek(e);
  e.preventDefault();
});
const tlEnd = e => {
  if (e.pointerId !== tlId) return;
  tlId = null;
  showNativeControls();
};
tl.addEventListener('pointerup', tlEnd);
tl.addEventListener('pointercancel', tlEnd);

paintDial();
paintPlayGlyph();

// ---- writing tags back -------------------------------------------------
// Only Run/Pass and the review flag. Everything else about a play is
// still a desktop job, and the server rejects any other key outright.

/* Suggestions, not a closed set. An opponent is usually "#12 QB" and no
   roster file carries that, so the field stays free text and the list
   only saves thumbs where it can. */
function loadNames() {
  fetch(withProject('api/roster'), { headers: AUTH })
    .then(r => r.json()).then(d => {
      const list = $('names');
      list.innerHTML = '';
      // Names already used in this project come first: they are spelled
      // the way this analyst spells them.
      (d.used || []).concat(d.squad || []).forEach(n => {
        const o = document.createElement('option');
        o.value = n;
        list.appendChild(o);
      });
    }).catch(() => {});
}

function buildEdit() {
  const box = $('rp');
  box.innerHTML = '';
  RP_VALUES.forEach(val => {
    const b = document.createElement('button');
    b.className = 'ebtn'; b.dataset.rp = val; b.dataset.on = '0';
    b.textContent = val;
    // Tapping the value a play already has clears it, so there is no
    // separate "none" button and no way to get stuck on a wrong answer.
    b.onclick = () => edit({ run_pass: b.dataset.on === '1' ? '' : val });
    box.appendChild(b);
  });
  $('rev').onclick = () => edit({ needs_review: $('rev').dataset.on !== '1' });

  // Committed on blur and on Enter rather than per keystroke: every
  // keystroke would queue a pending edit per letter, and the queue is
  // what gets synced.
  const commit = (el, key) => {
    const send = () => {
      if (!CURRENT) return;
      const was = key === 'player_name'
        ? (CURRENT.details.player_name || '')
        : (CURRENT.details.other_players || '');
      if (el.value.trim() === was) return;
      const patch = {};
      patch[key] = el.value;
      edit(patch);
    };
    el.onblur = send;
    el.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); el.blur(); } };
  };
  commit($('key'), 'player_name');
  commit($('inv'), 'other_players');
}

function paintEdit() {
  // Tagging works with a read-only server: it goes to the phone. Only
  // the Sync button needs the server to accept writes.
  const live = !!CURRENT;
  $('edit').className = live ? 'on' : '';
  if (!live) return;
  const rp = CURRENT.details.run_pass || '';
  Array.prototype.forEach.call($('rp').children,
    b => b.dataset.on = b.dataset.rp === rp ? '1' : '0');
  $('rev').dataset.on = CURRENT.tags.indexOf(REVIEW) >= 0 ? '1' : '0';
  // Not while someone is typing in it - repainting under a thumb would
  // throw away a half-typed name.
  if (document.activeElement !== $('key')) {
    $('key').value = CURRENT.details.player_name || '';
  }
  if (document.activeElement !== $('inv')) {
    $('inv').value = CURRENT.details.other_players || '';
  }
}

let statTimer = 0;
function status(text, bad) {
  const el = $('estat');
  el.textContent = text;
  el.className = bad ? 'bad' : '';
  clearTimeout(statTimer);
  // A failure stays up until the next tap; a success clears itself.
  if (text && !bad) statTimer = setTimeout(() => { el.textContent = ''; }, 1600);
}

/* An edit lands on the phone immediately and goes no further.

   Applied to the in-memory play as well so chips, filters and the list
   all reflect it at once - the phone's copy is the truth until a sync
   replaces it with the file's. */
function edit(patch) {
  if (!CURRENT) return;
  const p = CURRENT;
  const key = pendId(PROJECT, p.number);
  const queued = PENDING[key] || { project: PROJECT, number: p.number,
                                    patch: {}, at: 0 };
  Object.assign(queued.patch, patch);
  queued.at = Date.now();
  PENDING[key] = queued;
  savePending();

  if ('run_pass' in patch) p.details.run_pass = patch.run_pass;
  if ('player_name' in patch) {
    const v2 = String(patch.player_name || '').trim();
    if (v2) p.details.player_name = v2; else delete p.details.player_name;
  }
  if ('other_players' in patch) {
    const v3 = String(patch.other_players || '').trim();
    if (v3) p.details.other_players = v3; else delete p.details.other_players;
  }
  if ('needs_review' in patch) {
    const has = p.tags.indexOf(REVIEW) >= 0;
    if (patch.needs_review && !has) p.tags.push(REVIEW);
    if (!patch.needs_review && has) p.tags.splice(p.tags.indexOf(REVIEW), 1);
  }
  status('Saved on phone');
  buildChips(); render(); paintNow(); paintEdit(); paintSync();
}

/* Push the queue, one play per request, and keep whatever fails.

   Sequential on purpose: the failure that matters is the desktop having
   the project open, and firing twenty requests at it produces twenty
   copies of one message. The first refusal stops the run. */
function syncNow() {
  const keys = pendingFor(PROJECT);
  if (!keys.length) return;
  $('syncgo').disabled = true;
  $('syncdrop').disabled = true;
  let ok = 0;

  const step = i => {
    if (i >= keys.length) return finish(null);
    const item = PENDING[keys[i]];
    fetch(withProject('api/play/' + item.number), {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, AUTH),
      body: JSON.stringify(item.patch)
    }).then(r => {
      if (r.ok) return r.json();
      return r.json().catch(() => ({})).then(j => {
        throw new Error(j.error || ('HTTP ' + r.status));
      });
    }).then(fresh => {
      // Adopt what the file says, not what was sent. Painting the request
      // would show an edit as applied even when the project disagrees.
      const play = PLAYS.filter(x => x.number === item.number)[0];
      if (play) Object.assign(play, fresh);
      delete PENDING[keys[i]];
      savePending();
      ok++;
      step(i + 1);
    }).catch(e => finish(e));
  };

  const finish = err => {
    $('syncgo').disabled = false;
    $('syncdrop').disabled = false;
    buildChips(); render(); paintNow(); paintEdit(); paintSync();
    if (err) {
      const left = pendingFor(PROJECT).length;
      const msg = String(err.message || err);
      $('warn').style.display = 'block';
      // "Missing or bad token" is true and useless standing on a field.
      // What matters is that the work is not gone and what to do next.
      $('warn').textContent = /token/i.test(msg)
        ? 'This link is out of date, so nothing was saved to the project. '
          + 'Your ' + left + ' edit' + (left === 1 ? '' : 's')
          + ' are still on this phone - open the current URL and tap Sync '
          + 'again.'
        : msg + ' - ' + ok + ' synced, ' + left + ' still on this phone.';
    } else {
      $('warn').style.display = 'none';
      status(ok + ' synced');
    }
  };
  step(0);
}

$('syncgo').onclick = syncNow;
$('syncdrop').onclick = () => {
  const n = pendingFor(PROJECT).length;
  if (!n) return;
  // Discarding is the one action here that destroys work, so it asks.
  if (!confirm('Discard ' + n + ' edit' + (n === 1 ? '' : 's') +
               ' saved on this phone?')) return;
  pendingFor(PROJECT).forEach(k => delete PENDING[k]);
  savePending();
  loadPlays();
};
$('prev').onclick = () => step(-1);
$('next').onclick = () => step(1);
$('clear').onclick = () => { $('q').value = ''; render(); $('q').focus(); };
$('q').addEventListener('input', render);

v.addEventListener('timeupdate', () => {
  paintReadout();
  paintTimeline();
  const stop = parseFloat(v.dataset.stop || 'NaN');
  if (!isNaN(stop) && v.currentTime >= stop) { v.pause(); v.dataset.stop = ''; }
});

const fmt = ms => {
  const s = Math.floor(ms / 1000);
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
};
const esc = t => String(t).replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    """python -m tapesift.services.companion_server <project.tapesift>"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Serve a phone view of a TapeSift project on a private "
                    "LAN or Tailscale network. Read-only unless "
                    "--allow-writes.")
    parser.add_argument(
        "project", type=Path,
        help="a .tapesift file, or a folder of them (the usual case - "
             "the phone then gets a picker for every game in it)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--allow-writes", action="store_true",
        help="let the phone set Run/Pass and clear needs-review. Requires "
             "the project to be closed in the desktop app.")
    parser.add_argument(
        "--token", default="",
        help="use this write token instead of the saved one")
    parser.add_argument(
        "--new-token", action="store_true",
        help="mint a new write token, which revokes every URL already "
             "handed out")
    args = parser.parse_args(argv)

    library = ProjectLibrary(args.project, writable=args.allow_writes)
    if not args.project.exists():
        print(f"Nothing at {args.project}")
        return 1
    projects = library.files()
    if not projects:
        print(f"No .tapesift projects in {args.project}")
        return 1

    # Before opening anything ourselves: our own connection would create
    # the -shm file this looks for. Every project has to be closed, not
    # just the one that happens to open first, because the phone can
    # switch to any of them.
    busy = [p for p in projects if desktop_has_it_open(p)]
    if args.allow_writes and busy:
        for path in busy:
            print(f"Open in another process: {path.name}")
        print("\nThis is most likely the desktop app.")
        print("Writing to it now would not survive: the desktop holds every "
              "clip in memory and")
        print("its next save replaces all of them, erasing anything the "
              "phone wrote in between.")
        print("\nClose the project in TapeSift and start this again.")
        print("(If TapeSift crashed, open and close the project once to "
              "clear the stale lock file.)")
        return 1

    # Saved rather than minted per run, so the URL on the phone survives
    # a restart. Every code change here means a restart, and a token that
    # changed with it made the bookmark quietly read-only.
    token = ""
    if args.allow_writes:
        token = args.token or stable_token(rotate=args.new_token)
    server, url = serve(args.project, port=args.port,
                        writable=args.allow_writes, token=token)
    remote_urls = tailscale_urls(
        server.server_address[1],
        getattr(server.RequestHandlerClass, "token", ""))

    summaries = library.summaries()
    total = sum(row["plays"] for row in summaries)
    print(f"{len(summaries)} projects, {total} plays", flush=True)
    for row in summaries:
        flag = "" if row["film_available"] else "   ! film not found"
        print(f"  {row['plays']:>4}  {row['name']}{flag}")
    print(f"\n  On your phone, same wifi:  {url}\n", flush=True)
    for remote_url in remote_urls:
        print(f"  Away from home, Tailscale: {remote_url}", flush=True)
    if remote_urls:
        print(flush=True)
    if args.allow_writes:
        print("  WRITES ON. That URL can set Run/Pass and clear "
              "needs-review.")
        print("  The token in it is the only thing stopping anyone else on "
              "this private network.")
        if not args.token:
            print(f"  It is saved in {token_file()}, so this URL keeps")
            print("  working across restarts. --new-token revokes it.")
        print("  Leave the projects closed in TapeSift until you stop "
              "this.")
    else:
        print("  Read-only.")
    print("  Private network only - do not forward this port or enable "
          "Tailscale Funnel.")
    print("  Ctrl-C to stop.", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
