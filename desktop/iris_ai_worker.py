#!/usr/bin/env python3
"""iris-ai-worker: compute on this computer the AI a GPU-less server skips.

Signs in as a device of one account and processes that library's items that
have no AI data yet (see core/ai_jobs.py): it claims a batch, downloads the
originals, runs the indexer pipeline the server would run -- same model, CLIP
embedding, OCR, taxonomy and faces -- on a scratch database, and posts the
results back. The server rebuilds its search indices once per batch.

Needs this repository and its Python environment with the models' runtime
(PyTorch, preferably with CUDA); run it with that environment's python:

  python desktop/iris_ai_worker.py login https://iris.example
  python desktop/iris_ai_worker.py status       # how many items wait
  python desktop/iris_ai_worker.py once         # work until the queue is empty
  python desktop/iris_ai_worker.py run          # keep checking for new uploads
  python desktop/iris_ai_worker.py install-service [--enable]
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import getpass
import hashlib
import io
import os
import signal
import socket
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import iris_sync  # noqa: E402
from iris_sync import Client, SyncError  # noqa: E402


class WorkerConfig(iris_sync.Config):
    APP = "iris-ai-worker"


TEXT_COLUMNS = (
    "texto_extraido", "ocr_normalized", "descricao_ia", "tags", "visual_json",
    "objects", "style", "source_work", "humor", "context",
)
INT_COLUMNS = ("width", "height", "schema_version")

BATCH = 16
FLUSH_EVERY = 300  # seconds
# The indexer and the model libraries print a lot; a run shows one line per batch.
VERBOSE = False


@contextlib.contextmanager
def _quiet():
    if VERBOSE:
        yield
        return
    import warnings

    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        warnings.simplefilter("ignore")
        yield


def _b64(blob: bytes | None) -> str:
    return base64.b64encode(blob).decode() if blob else ""


# Florence-2 also reads the text of images that have some (merged with EasyOCR).
FLORENCE_OCR = True


# Models stay loaded between batches: loading them took longer than the batch.
_models: dict[tuple, object] = {}


def process_batch(files: list[Path], model_name: str, workdir: Path) -> dict[str, dict]:
    """Run the indexer on ``files`` into a scratch DB; results by file name.

    Each result carries what the server stores: the AI columns, both
    embeddings and the faces. A file the pipeline skipped is absent.
    """
    from core.indexer import IndexerConfig, load_models, process_images, resolve_device
    from core.indexer_db import init_db

    db_path = workdir / "work.db"
    init_db(db_path).close()
    config = IndexerConfig(
        media_dir=files[0].parent,
        db_path=db_path,
        model_name=model_name,
        batch_size=8,
        device=resolve_device("auto"),
        recursive=False,
        limit=None,
        rebuild_faiss_only=False,
        caption_model="none",
        whisper_model="none",
        sample_manifest=None,
        library_name="media",
        library_root=workdir,
        copy_to_library=False,
        extract_faces=True,
        florence_ocr=FLORENCE_OCR,
    )
    key = (model_name, config.device, FLORENCE_OCR)
    with _quiet():
        if key not in _models:
            _models.clear()  # a different model: free the GPU before loading it
            _models[key] = load_models(config)
        process_images(config, explicit_files=files, dedup_enabled=False, models=_models[key])
    return read_results(db_path)


def read_results(db_path: Path) -> dict[str, dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        has_faces = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'faces'"
        ).fetchone()
        results: dict[str, dict] = {}
        for row in conn.execute("SELECT * FROM memes"):
            if not row["embedding"]:
                continue
            result = {
                "model_name": row["model_name"],
                "embedding_dim": len(row["embedding"]) // 4,
                "embedding": _b64(row["embedding"]),
                "desc_embedding": _b64(row["desc_embedding"]),
                **{name: row[name] or "" for name in TEXT_COLUMNS},
                **{name: int(row[name] or 0) for name in INT_COLUMNS},
                "faces": [],
            }
            if has_faces:
                result["faces"] = [
                    {"bbox": face["bbox"], "det_score": face["det_score"],
                     "frame_time": face["frame_time"], "embedding": _b64(face["embedding"]),
                     "thumbnail": _b64(face["thumbnail"])}
                    for face in conn.execute(
                        "SELECT bbox, det_score, frame_time, embedding, thumbnail FROM faces WHERE meme_id = ?",
                        (row["id"],),
                    )
                ]
            results[row["arquivo"]] = result
        secondary = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'secondary_vectors'"
        ).fetchone()
        if secondary:
            names = {r["id"]: r["arquivo"] for r in conn.execute("SELECT id, arquivo FROM memes")}
            for row in conn.execute("SELECT meme_id, model_name, embedding, desc_embedding FROM secondary_vectors"):
                name = names.get(row["meme_id"])
                if name in results:
                    # The current model's vectors, so an item without AI can be
                    # searched before the library finishes moving.
                    results[name]["current_vectors"] = {
                        "model_name": row["model_name"],
                        "embedding": _b64(row["embedding"]),
                        "desc_embedding": _b64(row["desc_embedding"]),
                    }
        return results
    finally:
        conn.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def work_once(client: Client, *, batch: int = BATCH, log=print, limit: int | None = None,
              only: str = "",
              process: Callable[[list[Path], str, Path], dict[str, dict]] = process_batch) -> dict[str, int]:
    """Claim and process batches until the queue is empty or ``limit`` items were handled.

    ``only`` narrows a trial during a model change: "new" takes items with no
    AI at all, "processed" items that already have it (comparable side by side).
    """
    totals = {"done": 0, "failed": 0}
    # Flushing makes the server group faces and rebuild the library's search
    # index: on a small server that took longer than the batch itself, so it
    # happens every few minutes and when the run ends, not after every batch.
    last_flush = time.monotonic()
    dirty = False

    def finish() -> dict[str, int]:
        if dirty:
            client.request("POST", "/api/ai-jobs/flush")
        return totals

    while True:
        remaining = None if limit is None else limit - totals["done"] - totals["failed"]
        if remaining is not None and remaining <= 0:
            return finish()
        size = batch if remaining is None else min(batch, remaining)
        query = f"limit={size}" + (f"&only={only}" if only else "")
        status, body = client.request("POST", f"/api/ai-jobs/claim?{query}")
        if status != 200:
            raise SyncError(body.get("detail") or f"HTTP {status}")
        jobs = body.get("jobs", [])
        queue = int(body.get("pending", 0))
        _apply_server_options(body.get("options") or {})
        if not jobs:
            return finish()
        started = time.monotonic()

        def fail(job: dict, error: str) -> None:
            totals["failed"] += 1
            client.request("POST", f"/api/ai-jobs/{job['id']}/fail", json_body={"error": error[:500]})
            log(f"Falha no item {job['id']}: {error}")

        with tempfile.TemporaryDirectory(prefix="iris-ai-") as scratch:
            workdir = Path(scratch)
            media = workdir / "incoming"
            media.mkdir()
            files: dict[str, dict] = {}
            for job in jobs:
                # Named by id: unique, and maps a result back to its job.
                target = media / f"{job['id']}{Path(job['filename']).suffix.lower()}"
                try:
                    client.download(f"/api/ai-jobs/{job['id']}/file", target)
                except RuntimeError as exc:
                    fail(job, str(exc))
                    continue
                if job.get("sha256") and _sha256(target) != job["sha256"]:
                    fail(job, "o arquivo baixado não confere com o da biblioteca")
                    continue
                files[target.name] = job
            # Libraries can be on different models while one is being moved:
            # each model's items go through the pipeline together.
            results: dict[str, dict] = {}
            by_model: dict[str, list[str]] = {}
            for name, job in files.items():
                by_model.setdefault(job.get("model_name") or body.get("model_name", ""), []).append(name)
            for index, (model_name, names) in enumerate(by_model.items()):
                group_dir = workdir / f"model-{index}"
                group_dir.mkdir()
                try:
                    results.update(process(sorted(media / name for name in names), model_name, group_dir))
                except Exception as exc:  # the whole group: report each item
                    for name in names:
                        fail(files.pop(name), f"{type(exc).__name__}: {exc}")
            for name, job in files.items():
                result = results.get(name)
                if result is None:
                    fail(job, "o pipeline não conseguiu processar este arquivo")
                    continue
                result["sha256"] = job.get("sha256", "")
                status, body = client.request("POST", f"/api/ai-jobs/{job['id']}/result", json_body=result)
                if status == 200:
                    totals["done"] += 1
                else:
                    fail(job, body.get("detail") or f"HTTP {status}")
        dirty = True
        if time.monotonic() - last_flush > FLUSH_EVERY:
            client.request("POST", "/api/ai-jobs/flush")
            last_flush, dirty = time.monotonic(), False
        handled = totals["done"] + totals["failed"]
        goal = f"/{limit}" if limit else ""
        log(f"{len(jobs)} item(ns) em {time.monotonic() - started:.0f}s · {handled}{goal} nesta execução"
            f" · {max(0, queue - len(jobs))} na fila")


# ── Commands ────────────────────────────────────────────────────────────────


def _client() -> Client:
    config = WorkerConfig.load()
    if not (config.server and config.device_id and config.refresh_token):
        raise SyncError("Entre primeiro: iris_ai_worker.py login URL")
    return Client(config)


def cmd_login(args) -> None:
    username = args.username or input("Usuário: ")
    password = getpass.getpass("Senha: ")
    name = args.device_name or f"{socket.gethostname()} (IA)"
    config = iris_sync.login(args.server, username, password, name, WorkerConfig,
                             confirm=iris_sync.confirm_identity, platform="ai-worker")
    print(f"Conectado a {config.server} como “{name}”.")


def cmd_status(args) -> None:
    status, body = _client().request("GET", "/api/ai-jobs/status")
    if status != 200:
        raise SyncError(body.get("detail") or f"HTTP {status}")
    print(f"Modelo: {body['model_name']}")
    print(f"Sem IA nenhuma: {body.get('without_ai', body['pending'])}")
    print(f"Na fila (total): {body['pending']}")
    if body.get("migration"):
        m = body["migration"]
        print(f"Mudança para {m['target']}: {m['done']} de {m['total']} itens com vetores novos")


# Set from the command line; otherwise the server's choice applies.
_FLORENCE_FORCED_OFF = False


def _apply_server_options(options: dict) -> None:
    global FLORENCE_OCR
    if not _FLORENCE_FORCED_OFF and "florence_ocr" in options:
        FLORENCE_OCR = bool(options["florence_ocr"])


def _apply_options(args) -> None:
    global FLORENCE_OCR, VERBOSE, _FLORENCE_FORCED_OFF
    _FLORENCE_FORCED_OFF = getattr(args, "no_florence_ocr", False)
    FLORENCE_OCR = not _FLORENCE_FORCED_OFF
    VERBOSE = getattr(args, "verbose", False)
    if not VERBOSE:
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        os.environ.setdefault("TQDM_DISABLE", "1")


def cmd_once(args) -> None:
    _apply_options(args)
    only = {"novas": "new", "processadas": "processed"}.get(args.only, "")
    totals = work_once(_client(), batch=args.batch, limit=args.limit, only=only)
    print(f"Pronto: {totals['done']} processados, {totals['failed']} com falha.")


def cmd_run(args) -> None:
    _apply_options(args)
    client = _client()
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        try:
            totals = work_once(client, batch=args.batch)
            if totals["done"] or totals["failed"]:
                print(f"{totals['done']} processados, {totals['failed']} com falha", flush=True)
        except SyncError as exc:
            print(str(exc), file=sys.stderr, flush=True)
        deadline = time.monotonic() + args.interval
        while not stopping and time.monotonic() < deadline:
            time.sleep(1)


SERVICE = """[Unit]
Description=Iris: processa por IA a biblioteca do servidor
After=network-online.target
Wants=network-online.target

[Service]
ExecStart={python} {script} run --interval {interval}
Restart=on-failure
RestartSec=120
Nice=10

[Install]
WantedBy=default.target
"""


def cmd_install_service(args) -> None:
    import os
    import subprocess

    unit_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = unit_dir / "iris-ai-worker.service"
    unit.write_text(SERVICE.format(python=sys.executable, script=Path(__file__).resolve(),
                                   interval=args.interval), encoding="utf-8")
    print(f"Serviço escrito em {unit} (usa {sys.executable})")
    if args.enable:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "iris-ai-worker.service"], check=True)
        print("Ativo. Acompanhe com: journalctl --user -u iris-ai-worker -f")
    else:
        print("Para ativar: systemctl --user daemon-reload && systemctl --user enable --now iris-ai-worker")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iris-ai-worker", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("login", help="entrar numa conta do servidor")
    p.add_argument("server")
    p.add_argument("--username")
    p.add_argument("--device-name")
    p.set_defaults(func=cmd_login)
    commands.add_parser("status", help="quantos itens esperam IA").set_defaults(func=cmd_status)
    p = commands.add_parser("once", help="processar até a fila esvaziar (ou --limit itens)")
    p.add_argument("--batch", type=int, default=BATCH)
    p.add_argument("--limit", type=int, help="parar depois de tantos itens (para um teste)")
    p.add_argument("--only", choices=["novas", "processadas", "todas"], default="todas",
                   help="durante uma troca de modelo: novas = só fotos sem IA; "
                        "processadas = só as que já têm (dá para comparar lado a lado)")
    p.add_argument("--no-florence-ocr", action="store_true", help="só EasyOCR (mais rápido)")
    p.add_argument("--verbose", action="store_true", help="mostrar o log completo do indexador")
    p.set_defaults(func=cmd_once)
    p = commands.add_parser("run", help="processar continuamente (como serviço)")
    p.add_argument("--batch", type=int, default=BATCH)
    p.add_argument("--no-florence-ocr", action="store_true", help="só EasyOCR (mais rápido)")
    p.add_argument("--verbose", action="store_true", help="mostrar o log completo do indexador")
    p.add_argument("--interval", type=int, default=600, help="segundos entre verificações")
    p.set_defaults(func=cmd_run)
    p = commands.add_parser("install-service", help="criar o serviço systemd --user")
    p.add_argument("--interval", type=int, default=600)
    p.add_argument("--enable", action="store_true")
    p.set_defaults(func=cmd_install_service)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except SyncError as exc:
        print(f"iris-ai-worker: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
