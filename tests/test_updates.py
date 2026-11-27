"""Release checks and the request channel to the host update agent."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core import updates
from core.updates import UpdateChecker, newest_stable, parse_version, request_update
from routers.admin import router as admin_router


def test_only_stable_tags_count_and_the_highest_wins():
    tags = ["v0.8.0", "v0.10.0-rc.1", "v0.9.0", "v0.10.0", "latest", "v1.0", "v0.9.12"]
    assert newest_stable(tags) == "0.10.0"
    assert newest_stable(["v1.0.0-rc.2", "nightly"]) is None
    # Numeric, not alphabetical: 0.10.0 is newer than 0.9.12.
    assert parse_version("0.10.0") > parse_version("v0.9.12")


def test_a_newer_release_is_reported_as_available():
    checker = UpdateChecker("0.9.0", fetch=lambda repo: ["v0.9.0", "v0.10.0"], clock=lambda: 0, enabled=True)
    info = checker.check()
    assert (info.current, info.latest, info.available, info.error) == ("0.9.0", "0.10.0", True, None)
    assert info.checked_at == "1970-01-01T00:00:00+00:00"


def test_the_running_release_or_an_older_one_is_not_an_update():
    same = UpdateChecker("0.9.0", fetch=lambda repo: ["v0.9.0"], enabled=True).check()
    older = UpdateChecker("0.9.0", fetch=lambda repo: ["v0.8.0"], enabled=True).check()
    assert not same.available and not older.available
    # A development checkout ahead of every tag is not offered a downgrade either.
    assert not UpdateChecker("1.0.0", fetch=lambda repo: ["v0.9.0"], enabled=True).check().available


def test_a_failed_check_keeps_the_last_answer_and_says_why():
    answers = iter([["v0.10.0"]])

    def fetch(repo):
        try:
            return next(answers)
        except StopIteration:
            raise OSError("network down") from None

    checker = UpdateChecker("0.9.0", fetch=fetch, enabled=True)
    checker.check()
    info = checker.check()
    assert info.latest == "0.10.0" and info.available
    assert info.error


def test_disabled_checks_never_call_out():
    def fetch(repo):
        raise AssertionError("must not be called")

    info = UpdateChecker("0.9.0", fetch=fetch, enabled=False).check()
    assert info.enabled is False and info.latest is None


def _install_agent(data_dir: Path) -> Path:
    folder = data_dir / updates.UPDATER_DIR
    folder.mkdir(parents=True)
    (folder / updates.AGENT_FILE).write_text("{}")
    return folder


def test_a_request_needs_the_host_agent(tmp_path: Path):
    with pytest.raises(updates.UpdateRequestError, match="updater install"):
        request_update(tmp_path, requested_by="alice")
    assert not (tmp_path / updates.UPDATER_DIR / updates.REQUEST_FILE).exists()


def test_a_request_is_written_whole_and_carries_no_command(tmp_path: Path):
    folder = _install_agent(tmp_path)
    request_update(tmp_path, requested_by="alice", clock=lambda: 0)
    body = json.loads((folder / updates.REQUEST_FILE).read_text())
    assert body == {"requested_by": "alice", "requested_at": "1970-01-01T00:00:00+00:00"}
    assert not list(folder.glob(".*.tmp"))


def test_a_second_request_is_refused_while_one_is_pending_or_running(tmp_path: Path):
    folder = _install_agent(tmp_path)
    request_update(tmp_path, requested_by="alice")
    with pytest.raises(updates.UpdateRequestError, match="andamento"):
        request_update(tmp_path, requested_by="alice")
    (folder / updates.REQUEST_FILE).unlink()
    (folder / updates.STATUS_FILE).write_text(json.dumps({"state": "running"}))
    with pytest.raises(updates.UpdateRequestError, match="andamento"):
        request_update(tmp_path, requested_by="alice")


# -- API ---------------------------------------------------------------------------


def _client(tmp_path: Path, *, admin: bool) -> TestClient:
    app = FastAPI()
    app.state.multiuser_enabled = True
    app.state.data_dir = tmp_path
    app.state.update_checker = UpdateChecker("0.9.0", fetch=lambda repo: ["v0.10.0"], enabled=True)

    @app.middleware("http")
    async def sign_in(request: Request, call_next):
        request.state.iris_user = SimpleNamespace(username="alice", is_admin=admin)
        return await call_next(request)

    app.include_router(admin_router)
    return TestClient(app)


def test_admins_see_and_refresh_the_release_status(tmp_path: Path):
    client = _client(tmp_path, admin=True)
    assert client.get("/api/admin/update").json()["latest"] is None
    body = client.post("/api/admin/update/check").json()
    assert (body["current"], body["latest"], body["available"]) == ("0.9.0", "0.10.0", True)
    assert body["updater"] == {"installed": False, "pending": False, "status": None}


def test_applying_without_the_agent_explains_how_to_enable_it(tmp_path: Path):
    response = _client(tmp_path, admin=True).post("/api/admin/update/apply")
    assert response.status_code == 409
    assert "updater install" in response.json()["detail"]


def test_applying_leaves_a_request_for_the_agent(tmp_path: Path):
    _install_agent(tmp_path)
    response = _client(tmp_path, admin=True).post("/api/admin/update/apply")
    assert response.status_code == 202
    assert response.json()["updater"]["pending"] is True


def test_other_accounts_cannot_see_or_trigger_updates(tmp_path: Path):
    _install_agent(tmp_path)
    client = _client(tmp_path, admin=False)
    assert client.get("/api/admin/update").status_code == 403
    assert client.post("/api/admin/update/apply").status_code == 403
    assert not (tmp_path / updates.UPDATER_DIR / updates.REQUEST_FILE).exists()
