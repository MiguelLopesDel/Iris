"""Pairing a phone: the code a signed-in session shows, the optional CA it points to, and
the challenge a device uses to check it is talking to this installation."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from core import instance_settings, pairing, server_identity
from routers.admin import _admin

router = APIRouter(tags=["pairing"])


def _data_dir(request: Request) -> Path:
    return Path(request.app.state.data_dir)


def _user(request: Request):
    user = getattr(request.state, "iris_user", None)
    if user is None:
        raise HTTPException(401, "Autenticação necessária")
    return user


@router.get("/api/pairing")
def pairing_code(request: Request, current: str = Query("", max_length=300)):
    """The pairing code for this server. ``current`` is the address the browser is using."""
    _user(request)
    data_dir = _data_dir(request)
    configured = str(
        instance_settings.resolve_all(request.app.state.users_db_path)["pairing_addresses"].value
    ).split()
    addresses = pairing.pairing_addresses(configured, current or None)
    ca = pairing.ca_fingerprint(data_dir)
    instance = pairing.instance_id(data_dir)
    key = server_identity.server_identity(data_dir).fingerprint
    uri = pairing.pairing_uri(instance, addresses, ca, key)
    return {
        "instance_id": instance,
        "addresses": addresses,
        "ca_sha256": ca,
        "key_sha256": key,
        "uri": uri,
        "qr_svg": pairing.qr_svg(uri) if addresses else None,
    }


@router.get("/api/pairing/ca.pem")
def pairing_ca(request: Request):
    """The authority a pairing code may name. Public: a CA certificate is public by nature."""
    pem = pairing.load_ca(_data_dir(request))
    if pem is None:
        raise HTTPException(404, "Nenhum certificado de autoridade configurado")
    return Response(pem, media_type="application/x-pem-file")


class CaIn(BaseModel):
    pem: str


@router.put("/api/admin/pairing/ca")
def set_pairing_ca(request: Request, payload: CaIn):
    _admin(request)
    if len(payload.pem) > 64 * 1024:
        raise HTTPException(413, "Certificado grande demais")
    try:
        sha256 = pairing.save_ca(_data_dir(request), payload.pem)
    except pairing.PairingError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ca_sha256": sha256}


@router.delete("/api/admin/pairing/ca")
def remove_pairing_ca(request: Request):
    _admin(request)
    pairing.remove_ca(_data_dir(request))
    return {"ca_sha256": None}


@router.get("/api/identity")
def identity(
    request: Request,
    nonce: str = Query(..., max_length=128),
    address: str = Query(..., max_length=300),
):
    """Sign a device's challenge with this installation's key (see core.server_identity).

    Public on purpose: a device asks before it signs in, and nothing here is
    secret. ``address`` is the scheme, host and port the device used.
    """
    data_dir = _data_dir(request)
    try:
        return server_identity.answer_challenge(
            server_identity.server_identity(data_dir), pairing.instance_id(data_dir), nonce, address
        )
    except server_identity.IdentityError as exc:
        raise HTTPException(422, str(exc)) from exc

