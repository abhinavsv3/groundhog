"""URLs are accepted anywhere a repository path is."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from groundhog import repos


@pytest.mark.parametrize("spec", [
    "https://github.com/pallets/click",
    "https://github.com/pallets/click.git",
    "https://github.com/pallets/click/",
    "github.com/pallets/click",
    "pallets/click",
    "git@github.com:pallets/click.git",
    "ssh://git@github.com/pallets/click.git",
])
def test_github_spellings_resolve_to_the_same_clone(spec):
    parsed = repos.parse(spec)
    assert parsed is not None
    _, host, owner, name = parsed
    assert (host, owner, name) == ("github.com", "pallets", "click")


def test_https_clone_url_is_normalised():
    url, *_ = repos.parse("github.com/pallets/click/")
    assert url == "https://github.com/pallets/click.git"


def test_ssh_urls_are_passed_through_unchanged():
    url, *_ = repos.parse("git@github.com:pallets/click.git")
    assert url == "git@github.com:pallets/click.git"


def test_other_hosts_are_not_rewritten_to_github():
    url, host, owner, name = repos.parse("https://gitlab.com/inkscape/inkscape")
    assert host == "gitlab.com" and url.startswith("https://gitlab.com/")


@pytest.mark.parametrize("spec", [".", "..", "../foo", "/tmp", "~/src/x", "./a/b"])
def test_local_paths_are_left_alone(spec):
    assert repos.parse(spec) is None


def test_an_existing_directory_wins_over_the_short_form(tmp_path, monkeypatch):
    (tmp_path / "pallets" / "click").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert repos.parse("pallets/click") is None


def test_resolve_clones_once_and_fetches_after(tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=origin, check=True)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a",
                    "commit", "-q", "--allow-empty", "-m", "x"], cwd=origin, check=True)
    monkeypatch.setenv("GROUNDHOG_CACHE", str(tmp_path / "cache"))
    # Route the GitHub URL at our local origin.
    monkeypatch.setattr(repos, "parse", lambda spec: (str(origin), "github.com", "o", "n")
                        if spec == "o/n" else None)

    first = repos.resolve("o/n", quiet=True)
    assert (first / ".git").exists()
    assert first == tmp_path / "cache" / "repos" / "github.com" / "o" / "n"
    second = repos.resolve("o/n", quiet=True)
    assert second == first


def test_remote_name_reads_origin(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:pallets/click.git"],
                   cwd=tmp_path, check=True)
    assert repos.remote_name(tmp_path) == "pallets/click"


def test_remote_name_is_none_without_origin(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    assert repos.remote_name(tmp_path) is None


def test_repo_arg_rejects_a_plain_directory(tmp_path):
    with pytest.raises(Exception) as info:
        repos.repo_arg(str(tmp_path))
    assert "not a git repository" in str(info.value)
