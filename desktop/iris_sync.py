#!/usr/bin/env python3
"""iris-sync: keep folders of this computer backed up to an Iris server.

The desktop counterpart of the phone's backup. It signs in as a device, looks
through the chosen folders, and sends the photos and videos the server does
not have yet over the same resumable protocol the phone uses
(/api/sync/uploads/*): hash, reserve in batches, send in chunks, complete.
The server answers files it already holds at reservation, so nothing is sent
twice; a local record of what was sent spares re-hashing unchanged files.

Standard library only, so it runs without installing
anything (Python 3.11+). Commands:

  iris-sync login https://iris.example     sign in (asks user and password)
  iris-sync add ~/Imagens                  choose a folder to keep in sync
  iris-sync remove ~/Imagens               stop syncing a folder
  iris-sync status                         server, folders and last results
  iris-sync once                           one pass over the folders
  iris-sync run [--interval 300]           pass after pass, as a service does
  iris-sync install-service [--enable]     systemd --user unit for `run`
  iris-sync logout                         forget this device's session
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import json
import os
import signal
import socket
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".avif", ".bmp",
    ".tif", ".tiff", ".dng", ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".3gp",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".3gp"}
RESERVE_BATCH = 16
# A file changed this recently may still be being written (a camera import,
# a download): it waits for the next pass.
SETTLE_SECONDS = 60
MAX_ATTEMPTS = 5
# Server states meaning the library has the file, now or from an earlier run.
ALREADY_THERE = {"duplicate", "ready", "pending_processing", "processing"}


def config_dir(app: str = "iris-sync") -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / app


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "iris-sync"


# ── Configuration (server, device session, folders) ─────────────────────────


@dataclass
class Config:
    # The AI worker (iris_ai_worker.py) keeps its own device session apart.
    APP = "iris-sync"

    server: str = ""
    device_id: str = ""
    refresh_token: str = ""
    folders: list[str] = field(default_factory=list)
    # The server's own certificate authority, when it is not a public one
    # (an Iris local authority, say): trusted after its identity code was confirmed.
    ca_file: str = ""

    @classmethod
    def load(cls) -> Config:
        path = config_dir(cls.APP) / "config.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        return cls(
            server=data.get("server", ""),
            device_id=data.get("device_id", ""),
            refresh_token=data.get("refresh_token", ""),
            folders=list(data.get("folders", [])),
            ca_file=data.get("ca_file", ""),
        )

    def save(self) -> None:
        """Write atomically and readable by this user only: it holds a token."""
        directory = config_dir(self.APP)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "config.json.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(self.__dict__, handle, indent=2)
        os.replace(temporary, directory / "config.json")


# ── HTTP client with device tokens ──────────────────────────────────────────


class SyncError(Exception):
    """A failure that stops the pass (server unreachable, session revoked)."""


class Client:
    def __init__(self, config: Config, *, sleep=time.sleep):
        self.config = config
        self.access_token = ""
        self.sleep = sleep

    def _open(self, method: str, path: str, body: bytes | None, headers: dict[str, str]):
        request = urllib.request.Request(
            self.config.server.rstrip("/") + path, data=body, method=method, headers=headers
        )
        return urllib.request.urlopen(request, timeout=120, context=tls_context(self.config.ca_file))

    def refresh(self) -> None:
        """Trade the refresh token for a new access token; it rotates, so save it."""
        body = urllib.parse.urlencode(
            {"refresh_token": self.config.refresh_token, "device_id": self.config.device_id}
        ).encode()
        try:
            with self._open("POST", "/api/auth/devices/refresh", body,
                            {"Content-Type": "application/x-www-form-urlencoded"}) as response:
                tokens = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise SyncError(
                    "A sessão deste computador foi encerrada no servidor. Entre de novo com "
                    "`iris-sync login`."
                ) from exc
            raise SyncError(f"O servidor recusou renovar a sessão: HTTP {exc.code}") from exc
        except OSError as exc:
            raise SyncError(f"Servidor inacessível: {exc}") from exc
        self.config.refresh_token = tokens["refresh_token"]
        self.config.save()
        self.access_token = tokens["access_token"]

    def request(self, method: str, path: str, *, json_body=None, data: bytes | None = None,
                content_type: str = "") -> tuple[int, dict]:
        """One API call: refreshes an expired token once, retries a busy server."""
        if json_body is not None:
            data, content_type = json.dumps(json_body).encode(), "application/json"
        refreshed = False
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if not self.access_token:
                self.refresh()
                refreshed = True
            headers = {"Authorization": f"Bearer {self.access_token}"}
            if content_type:
                headers["Content-Type"] = content_type
            try:
                with self._open(method, path, data, headers) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and not refreshed:
                    self.access_token = ""
                    continue
                if exc.code >= 500 and exc.code != 507 and attempt < MAX_ATTEMPTS:
                    self.sleep(min(60, 2 ** attempt))
                    continue
                try:
                    detail = json.load(exc).get("detail", "")
                except (ValueError, AttributeError):
                    detail = ""
                return exc.code, {"detail": detail or f"HTTP {exc.code}"}
            except OSError as exc:
                if attempt < MAX_ATTEMPTS:
                    self.sleep(min(60, 2 ** attempt))
                    continue
                raise SyncError(f"Servidor inacessível: {exc}") from exc
        raise SyncError("O servidor não respondeu depois de várias tentativas")

    def download(self, path: str, target: Path) -> None:
        """Save a file the API serves (bytes, not JSON) to ``target``."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if not self.access_token:
                self.refresh()
            try:
                with self._open("GET", path, None, {"Authorization": f"Bearer {self.access_token}"}) as response:
                    with target.open("wb") as handle:
                        while block := response.read(1 << 20):
                            handle.write(block)
                return
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 1:
                    self.access_token = ""
                    continue
                if exc.code >= 500 and attempt < MAX_ATTEMPTS:
                    self.sleep(min(60, 2 ** attempt))
                    continue
                raise RuntimeError(f"Download recusado: HTTP {exc.code}") from exc
            except OSError as exc:
                if attempt < MAX_ATTEMPTS:
                    self.sleep(min(60, 2 ** attempt))
                    continue
                raise SyncError(f"Servidor inacessível: {exc}") from exc


def tls_context(ca_file: str = "") -> ssl.SSLContext:
    return ssl.create_default_context(cafile=ca_file or None)


def _unverified_get(url: str) -> bytes:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(url, timeout=30, context=context) as response:
        return response.read()


def trust_server(server: str, store_dir: Path, confirm) -> str:
    """Make sure the server's certificate is trusted; returns a CA file or "".

    A server with a certificate from a public authority needs nothing. One with
    its own authority (an Iris local CA) offers it at /api/tls/ca.crt; it is
    trusted only after the person confirms the server's identity code -- the
    one Iris shows on its pages -- matches, as a phone does when pairing. No
    password is sent before that.
    """
    base = server.rstrip("/")
    try:
        urllib.request.urlopen(base + "/healthz", timeout=30, context=tls_context())
        return ""
    except urllib.error.URLError as exc:
        if not isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise SyncError(f"Servidor inacessível: {exc.reason}") from exc
    try:
        identity = json.loads(_unverified_get(base + "/healthz")).get("identity_code", "")
        pem = _unverified_get(base + "/api/tls/ca.crt")
    except (OSError, ValueError) as exc:
        raise SyncError(f"O servidor não ofereceu um certificado de autoridade: {exc}") from exc
    if not identity or not confirm(identity):
        raise SyncError("Identidade do servidor não confirmada; nada foi enviado.")
    store_dir.mkdir(parents=True, exist_ok=True)
    ca_file = store_dir / "server-ca.pem"
    ca_file.write_bytes(pem)
    try:
        urllib.request.urlopen(base + "/healthz", timeout=30, context=tls_context(str(ca_file)))
    except urllib.error.URLError as exc:
        ca_file.unlink(missing_ok=True)
        raise SyncError(f"Mesmo com a autoridade do servidor, o certificado não confere: {exc.reason}") from exc
    return str(ca_file)


def login(server: str, username: str, password: str, device_name: str,
          config_class: type[Config] = Config, confirm=lambda _code: False) -> Config:
    ca_file = trust_server(server, config_dir(config_class.APP), confirm) if server.startswith("https") else ""
    body = urllib.parse.urlencode({
        "username": username, "password": password,
        "device_name": device_name, "platform": "desktop",
    }).encode()
    request = urllib.request.Request(
        server.rstrip("/") + "/api/auth/devices/login", data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=tls_context(ca_file)) as response:
            tokens = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("detail", "")
        except ValueError:
            detail = ""
        raise SyncError(detail or f"Login recusado: HTTP {exc.code}") from exc
    except OSError as exc:
        raise SyncError(f"Servidor inacessível: {exc}") from exc
    config = config_class.load()
    config.server = server.rstrip("/")
    config.device_id = tokens["device_id"]
    config.refresh_token = tokens["refresh_token"]
    config.ca_file = ca_file
    config.save()
    return config


# ── Local record of what was sent ───────────────────────────────────────────


class State:
    """path → (size, mtime) last sent or found on the server, and failures."""

    def __init__(self, path: Path | None = None):
        path = path or state_dir() / "state.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS files (
                   path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                   sha256 TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL,
                   error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)"""
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS passes (finished_at TEXT, summary TEXT)"
        )

    def is_done(self, path: str, size: int, mtime_ns: int) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM files WHERE path = ? AND size = ? AND mtime_ns = ? AND outcome = 'done'",
            (path, size, mtime_ns),
        ).fetchone()
        return row is not None

    def record(self, path: str, size: int, mtime_ns: int, sha256: str, outcome: str,
               error: str = "") -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?, ?)",
            (path, size, mtime_ns, sha256, outcome, error, _now()),
        )
        self.db.commit()

    def record_pass(self, summary: str) -> None:
        self.db.execute("INSERT INTO passes VALUES (?, ?)", (_now(), summary))
        self.db.execute(
            "DELETE FROM passes WHERE rowid NOT IN (SELECT rowid FROM passes ORDER BY rowid DESC LIMIT 20)"
        )
        self.db.commit()

    def last_pass(self) -> tuple[str, str] | None:
        return self.db.execute(
            "SELECT finished_at, summary FROM passes ORDER BY rowid DESC LIMIT 1"
        ).fetchone()

    def failures(self) -> list[tuple[str, str]]:
        return self.db.execute(
            "SELECT path, error FROM files WHERE outcome = 'failed' ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


# ── One pass ────────────────────────────────────────────────────────────────


@dataclass
class Candidate:
    path: Path
    root: Path
    size: int
    mtime_ns: int
    sha256: str = ""
    client_id: str = ""
    retried: bool = False


@dataclass
class Summary:
    sent: int = 0
    already: int = 0
    failed: int = 0
    waiting: int = 0

    def text(self) -> str:
        parts = [f"{self.sent} enviados", f"{self.already} já estavam no Iris"]
        if self.failed:
            parts.append(f"{self.failed} com falha")
        if self.waiting:
            parts.append(f"{self.waiting} ainda sendo gravados (ficam para a próxima)")
        return ", ".join(parts)


def scan(folders: list[str], state: State, now: float | None = None) -> tuple[list[Candidate], int]:
    """New or changed media under the folders; and how many are still settling."""
    now = time.time() if now is None else now
    found: list[Candidate] = []
    settling = 0
    for folder in folders:
        root = Path(folder)
        for directory, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                path = Path(directory) / name
                if name.startswith(".") or path.suffix.lower() not in MEDIA_EXTENSIONS:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if state.is_done(str(path), stat.st_size, stat.st_mtime_ns):
                    continue
                if now - stat.st_mtime < SETTLE_SECONDS:
                    settling += 1
                    continue
                found.append(Candidate(path, root, stat.st_size, stat.st_mtime_ns))
    return found, settling


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _fnv1a(text: str) -> str:
    value = 0x811C9DC5
    for byte in text.encode():
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return f"{value:08x}"


def _reservation(candidate: Candidate) -> dict:
    folder = candidate.path.parent
    relative = folder.relative_to(candidate.root.parent).as_posix()
    kind = "video" if candidate.path.suffix.lower() in VIDEO_EXTENSIONS else "image"
    return {
        "client_upload_id": candidate.client_id,
        "filename": candidate.path.name,
        "size": candidate.size,
        "sha256": candidate.sha256,
        "captured_at": dt.datetime.fromtimestamp(
            candidate.mtime_ns / 1e9, dt.UTC
        ).isoformat(timespec="seconds"),
        "source": {
            "id": f"pc:{socket.gethostname()}:{folder}"[:512],
            "name": folder.name,
            "relative_path": relative,
            "media_kind": kind,
        },
    }


def send(client: Client, candidate: Candidate, upload_id: str, offset: int, chunk_size: int) -> str:
    """Send the rest of a reserved file and complete it; returns the final state."""
    with candidate.path.open("rb") as handle:
        while offset < candidate.size:
            handle.seek(offset)
            chunk = handle.read(max(1, chunk_size))
            status, body = client.request(
                "PUT", f"/api/sync/uploads/{upload_id}?offset={offset}",
                data=chunk, content_type="application/octet-stream",
            )
            if status == 200:
                offset = int(body.get("offset", offset + len(chunk)))
                continue
            if status != 409:
                raise RuntimeError(body.get("detail") or f"HTTP {status}")
            # Offset mismatch (a retried chunk had landed): ask where it stands.
            status, body = client.request("GET", f"/api/sync/uploads/{upload_id}")
            if status != 200:
                raise RuntimeError(body.get("detail") or f"HTTP {status}")
            if body.get("state") == "finalizing":
                break
            if body.get("state") != "uploading":
                if body.get("state") in ALREADY_THERE:
                    return body["state"]
                raise RuntimeError(f"Estado inesperado do servidor: {body.get('state')}")
            offset = int(body.get("offset", 0))
    status, body = client.request("POST", f"/api/sync/uploads/{upload_id}/complete")
    if status != 200 or body.get("state") not in ALREADY_THERE:
        raise RuntimeError(body.get("detail") or f"Estado inesperado do servidor: {body.get('state')}")
    return body["state"]


def sync_once(client: Client, config: Config, state: State, *, log=print) -> Summary:
    summary = Summary()
    folders = [f for f in config.folders if Path(f).is_dir()]
    for missing in set(config.folders) - set(folders):
        log(f"Pasta não encontrada (fica para a próxima): {missing}")
    candidates, summary.waiting = scan(folders, state)
    if candidates:
        log(f"{len(candidates)} arquivo(s) novo(s) ou alterado(s) para conferir")

    def fail(candidate: Candidate, error: str) -> None:
        summary.failed += 1
        state.record(str(candidate.path), candidate.size, candidate.mtime_ns,
                     candidate.sha256, "failed", error)
        log(f"Falha: {candidate.path}: {error}")

    def done(candidate: Candidate, sent: bool) -> None:
        if sent:
            summary.sent += 1
        else:
            summary.already += 1
        state.record(str(candidate.path), candidate.size, candidate.mtime_ns,
                     candidate.sha256, "done")

    queue = list(candidates)
    while queue:
        batch: list[Candidate] = []
        while queue and len(batch) < RESERVE_BATCH:
            candidate = queue.pop(0)
            if not candidate.sha256:
                try:
                    candidate.sha256 = sha256_of(candidate.path)
                except OSError as exc:
                    fail(candidate, f"Não foi possível ler o arquivo: {exc}")
                    continue
                candidate.client_id = f"pc:{candidate.sha256[:48]}:{_fnv1a(str(candidate.path))}"
            batch.append(candidate)
        if not batch:
            continue
        status, body = client.request(
            "POST", "/api/sync/uploads/batch",
            json_body={"uploads": [_reservation(c) for c in batch]},
        )
        if status != 200:
            for candidate in batch:
                fail(candidate, body.get("detail") or f"HTTP {status}")
            continue
        answers = {item.get("client_upload_id"): item for item in body.get("uploads", [])}
        for candidate in batch:
            answer = answers.get(candidate.client_id)
            if not answer or answer.get("error_code"):
                fail(candidate, (answer or {}).get("error_message") or "Resposta incompleta do servidor")
            elif answer.get("state") == "uploading":
                try:
                    send(client, candidate, answer["upload_id"], int(answer.get("offset", 0)),
                         int(answer.get("chunk_size") or 8 << 20))
                    done(candidate, sent=True)
                except (OSError, RuntimeError) as exc:
                    fail(candidate, str(exc))
            elif answer.get("state") in ALREADY_THERE:
                done(candidate, sent=False)
            elif answer.get("state") == "failed" and not candidate.retried:
                # A transfer that arrived corrupted stays failed for good on
                # the server: reserve it once more under a new id.
                candidate.retried = True
                candidate.client_id = f"{candidate.client_id}:r{int(time.time()):x}"
                queue.append(candidate)
            else:
                fail(candidate, f"Estado inesperado do servidor: {answer.get('state')}")
    state.record_pass(summary.text())
    return summary


# ── Commands ────────────────────────────────────────────────────────────────


def _require_login(config: Config) -> None:
    if not (config.server and config.device_id and config.refresh_token):
        raise SyncError("Este computador ainda não entrou num servidor: use `iris-sync login URL`.")


def confirm_identity(code: str) -> bool:
    """Ask the person to compare the code with the one Iris shows."""
    print(f"O certificado deste servidor é de uma autoridade própria. Código de identidade: {code}")
    print("Confira se é o mesmo código que o Iris mostra (Sistema → Conectar um celular).")
    return input("É o mesmo? [s/N] ").strip().lower() in {"s", "sim", "y", "yes"}


def cmd_login(args) -> None:
    username = args.username or input("Usuário: ")
    password = getpass.getpass("Senha: ")
    name = args.device_name or f"{socket.gethostname()} (PC)"
    config = login(args.server, username, password, name, confirm=confirm_identity)
    print(f"Conectado a {config.server} como “{name}”. Agora escolha pastas com `iris-sync add PASTA`.")


def cmd_logout(args) -> None:
    config = Config.load()
    config.device_id = config.refresh_token = ""
    config.save()
    print("Sessão esquecida neste computador. Para encerrá-la no servidor, use Sistema → Dispositivos.")


def cmd_add(args) -> None:
    config = Config.load()
    folder = str(Path(args.folder).expanduser().resolve())
    if not Path(folder).is_dir():
        raise SyncError(f"Não é uma pasta: {folder}")
    if folder not in config.folders:
        config.folders.append(folder)
        config.save()
    print(f"Sincronizando: {folder}")


def cmd_remove(args) -> None:
    config = Config.load()
    folder = str(Path(args.folder).expanduser().resolve())
    config.folders = [f for f in config.folders if f != folder]
    config.save()
    print(f"Não sincroniza mais: {folder} (o que já foi enviado continua no Iris)")


def cmd_status(args) -> None:
    config = Config.load()
    state = State()
    print(f"Servidor: {config.server or '(nenhum; use iris-sync login)'}")
    print("Pastas:" if config.folders else "Pastas: nenhuma (use iris-sync add PASTA)")
    for folder in config.folders:
        print(f"  {folder}{'' if Path(folder).is_dir() else '  (não encontrada)'}")
    last = state.last_pass()
    print(f"Última passada: {last[0]} · {last[1]}" if last else "Última passada: nenhuma ainda")
    for path, error in state.failures():
        print(f"  falha: {path}: {error}")


def cmd_once(args) -> None:
    config = Config.load()
    _require_login(config)
    summary = sync_once(Client(config), config, State())
    print(f"Pronto: {summary.text()}.")


def cmd_run(args) -> None:
    config = Config.load()
    _require_login(config)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    client, state = Client(config), State()
    while not stopping:
        config.folders = Config.load().folders  # `add` takes effect without a restart
        try:
            summary = sync_once(client, config, state)
            print(f"{_now()} {summary.text()}", flush=True)
        except SyncError as exc:
            print(f"{_now()} {exc}", file=sys.stderr, flush=True)
        deadline = time.monotonic() + args.interval
        while not stopping and time.monotonic() < deadline:
            time.sleep(1)


SERVICE = """[Unit]
Description=Iris: sincroniza pastas deste computador
After=network-online.target
Wants=network-online.target

[Service]
ExecStart={python} {script} run --interval {interval}
Restart=on-failure
RestartSec=60
# Background work: out of the way of whatever the computer is doing.
Nice=10
IOSchedulingClass=idle

[Install]
WantedBy=default.target
"""


def cmd_install_service(args) -> None:
    unit_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = unit_dir / "iris-sync.service"
    unit.write_text(SERVICE.format(
        python=sys.executable, script=Path(__file__).resolve(), interval=args.interval,
    ), encoding="utf-8")
    print(f"Serviço escrito em {unit}")
    if args.enable:
        import subprocess

        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "iris-sync.service"], check=True)
        print("Ativo. Acompanhe com: journalctl --user -u iris-sync -f")
    else:
        print("Para ativar: systemctl --user daemon-reload && systemctl --user enable --now iris-sync")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iris-sync", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("login", help="entrar num servidor Iris")
    p.add_argument("server")
    p.add_argument("--username")
    p.add_argument("--device-name")
    p.set_defaults(func=cmd_login)
    commands.add_parser("logout", help="esquecer a sessão deste computador").set_defaults(func=cmd_logout)
    for name, func, text in (("add", cmd_add, "sincronizar uma pasta"),
                             ("remove", cmd_remove, "parar de sincronizar uma pasta")):
        p = commands.add_parser(name, help=text)
        p.add_argument("folder")
        p.set_defaults(func=func)
    commands.add_parser("status", help="servidor, pastas e últimos resultados").set_defaults(func=cmd_status)
    commands.add_parser("once", help="uma passada pelas pastas").set_defaults(func=cmd_once)
    p = commands.add_parser("run", help="passadas contínuas (como serviço)")
    p.add_argument("--interval", type=int, default=300, help="segundos entre passadas")
    p.set_defaults(func=cmd_run)
    p = commands.add_parser("install-service", help="criar o serviço systemd --user")
    p.add_argument("--interval", type=int, default=300)
    p.add_argument("--enable", action="store_true", help="também ativar e iniciar agora")
    p.set_defaults(func=cmd_install_service)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except SyncError as exc:
        print(f"iris-sync: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
