#!/usr/bin/env python3
"""Run a safe smoke test against a running Iris server.

It is intentionally read-only unless ``--upload`` is supplied. Use a disposable,
uniquely named image for upload/isolation checks; that option imports media into
the first account.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


class IrisClient:
    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path: str, *, data: bytes | None = None, content_type: str = "") -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(self.base_url + path, data=data, method="POST" if data is not None else "GET")
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def json(self, path: str, *, data: bytes | None = None, content_type: str = "") -> tuple[int, dict[str, Any]]:
        status, _, body = self.request(path, data=data, content_type=content_type)
        try:
            return status, json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{path} não retornou JSON (HTTP {status})") from exc

    def login(self, username: str, password: str) -> None:
        body = urllib.parse.urlencode({"username": username, "password": password}).encode()
        status, payload = self.json("/api/auth/login", data=body, content_type="application/x-www-form-urlencoded")
        if status != 200 or not payload.get("ok"):
            raise RuntimeError(f"login falhou (HTTP {status})")


def multipart_upload(paths: list[Path]) -> tuple[bytes, str]:
    boundary = "----iris-smoke-" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for path in paths:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'.encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                path.read_bytes(),
                b"\r\n",
            ]
        )
    chunks.extend([f"--{boundary}\r\n".encode(), b'Content-Disposition: form-data; name="low_resource"\r\n\r\ntrue\r\n', f"--{boundary}--\r\n".encode()])
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def require(status: int, expected: int, action: str) -> None:
    if status != expected:
        raise RuntimeError(f"{action}: esperado HTTP {expected}, recebido {status}")


def wait_for_import(client: IrisClient, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, job = client.json("/api/import/status")
        require(status, 200, "consultar importação")
        if job.get("status") == "completed":
            return
        if job.get("status") in {"failed", "cancelled"}:
            raise RuntimeError("importação falhou: " + str(job.get("message", "sem detalhe")))
        time.sleep(2)
    raise RuntimeError("importação não terminou dentro do tempo configurado")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8501")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--expect-private", action="store_true", help="exige login e confere que caminhos não vazam")
    parser.add_argument("--upload", action="append", type=Path, default=[], metavar="ARQUIVO")
    parser.add_argument("--second-username", help="segunda conta para verificar isolamento após --upload")
    parser.add_argument("--second-password")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--import-timeout", type=float, default=900)
    args = parser.parse_args()
    if bool(args.username) != bool(args.password):
        parser.error("--username e --password devem ser usados juntos")
    if args.upload and not args.username:
        parser.error("--upload exige as credenciais da primeira conta")
    if bool(args.second_username) != bool(args.second_password):
        parser.error("--second-username e --second-password devem ser usados juntos")
    if (args.second_username or args.second_password) and not args.upload:
        parser.error("a segunda conta só é útil junto com --upload")
    for path in args.upload:
        if not path.is_file():
            parser.error(f"arquivo não encontrado: {path}")

    anonymous = IrisClient(args.url, args.timeout)
    health_status, health = anonymous.json("/healthz")
    require(health_status, 200, "healthz")
    print(f"OK healthz: modo={health.get('mode')} status={health.get('status')}")
    if args.expect_private:
        status, _, _ = anonymous.request("/api/info")
        require(status, 401, "acesso anônimo a /api/info")
        print("OK acesso anônimo bloqueado")
    if not args.username:
        return 0

    first = IrisClient(args.url, args.timeout)
    first.login(args.username, args.password)
    status, info = first.json("/api/info")
    require(status, 200, "consultar biblioteca autenticada")
    if args.expect_private and (info.get("db_path") or info.get("media_root")):
        raise RuntimeError("/api/info revelou caminho físico em modo privado")
    print("OK login e biblioteca autenticada")
    if not args.upload:
        return 0

    body, content_type = multipart_upload(args.upload)
    status, payload = first.json("/api/import", data=body, content_type=content_type)
    require(status, 200, "enviar mídia")
    if not payload.get("job_id"):
        raise RuntimeError("upload não criou um trabalho de importação")
    print("OK upload aceito; aguardando indexação")
    wait_for_import(first, args.import_timeout)
    status, records = first.json("/api/records?per_page=500")
    require(status, 200, "listar mídia importada")
    uploaded_names = {path.name for path in args.upload}
    imported = [record for record in records.get("records", []) if record.get("arquivo") in uploaded_names]
    if not imported:
        raise RuntimeError("a mídia enviada não apareceu na galeria")
    thumbnail = imported[0].get("thumbnail_url")
    if thumbnail:
        status, _, _ = first.request(thumbnail)
        require(status, 200, "baixar miniatura da mídia importada")
    print("OK importação e leitura da mídia")
    if args.second_username:
        second = IrisClient(args.url, args.timeout)
        second.login(args.second_username, args.second_password)
        status, other_records = second.json("/api/records?per_page=500")
        require(status, 200, "listar biblioteca da segunda conta")
        visible = {record.get("arquivo") for record in other_records.get("records", [])}
        leaked = uploaded_names & visible
        if leaked:
            raise RuntimeError("isolamento falhou: segunda conta viu " + ", ".join(sorted(leaked)))
        print("OK isolamento entre as duas contas")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"FALHOU: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
