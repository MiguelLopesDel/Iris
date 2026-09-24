from scripts.security import pre_push_check


def test_new_branch_scan_excludes_commits_already_on_target_remote(monkeypatch):
    local_sha = "a" * 40
    remote_ref = "refs/remotes/origin/main"
    calls = []

    def fake_git(*args):
        calls.append(args)
        if args[0] == "for-each-ref":
            return f"{remote_ref}\n"
        if args[0] == "rev-list":
            return "new-commit\n"
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(pre_push_check, "git", fake_git)
    update = f"refs/heads/feature {local_sha} refs/heads/feature {pre_push_check.ZERO_SHA}"

    assert pre_push_check.pushed_commits([update], "origin") == ["new-commit"]
    assert ("rev-list", local_sha, "--not", remote_ref) in calls


def test_new_branch_without_remote_tracking_refs_scans_full_history(monkeypatch):
    local_sha = "b" * 40
    calls = []

    def fake_git(*args):
        calls.append(args)
        if args[0] == "for-each-ref":
            return ""
        if args[0] == "rev-list":
            return "ancestor\nnew-commit\n"
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(pre_push_check, "git", fake_git)
    update = f"refs/heads/feature {local_sha} refs/heads/feature {pre_push_check.ZERO_SHA}"

    assert pre_push_check.pushed_commits([update], "origin") == ["ancestor", "new-commit"]
    assert ("rev-list", local_sha) in calls
