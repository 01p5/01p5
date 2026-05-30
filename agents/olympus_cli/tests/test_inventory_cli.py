"""
Tests for olympus-inventory.

End-to-end through main(argv); no subprocess. Each test gets its own
tmp store path, so the suite is hermetic and parallelizable.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from olympus_cli.inventory_cli import main


FAKE_KEY = textwrap.dedent(
    """
    -----BEGIN OPENSSH PRIVATE KEY-----
    AAAAB3NzaC1yc2EAAAADAQABAAABAQDinvcli
    -----END OPENSSH PRIVATE KEY-----
    """
).strip()


@pytest.fixture
def store_path(tmp_path: Path) -> str:
    return str(tmp_path / "inventory.json")


def _run(argv: list[str], capsys=None) -> tuple[int, str, str]:
    code = main(argv)
    out, err = capsys.readouterr() if capsys else ("", "")
    return code, out, err


# ---------------------------------------------------------------------------
# list-hosts / add-host
# ---------------------------------------------------------------------------


def test_list_hosts_empty(store_path, capsys):
    code, out, _ = _run(["--store", store_path, "list-hosts"], capsys)
    assert code == 0
    assert "no hosts" in out


def test_add_host_then_list_text(store_path, capsys):
    code, _, _ = _run(["--store", store_path, "add-host",
                       "--name", "cp", "--address", "10.0.0.1",
                       "--group", "control_plane",
                       "--var", "region=us-west-2"], capsys)
    assert code == 0
    code, out, _ = _run(["--store", store_path, "list-hosts"], capsys)
    assert code == 0
    assert "cp" in out
    assert "ubuntu@10.0.0.1" in out
    assert "control_plane" in out
    assert "region=us-west-2" in out


def test_add_host_then_list_json(store_path, capsys):
    _run(["--store", store_path, "add-host", "--name", "cp",
          "--address", "10.0.0.1"], capsys)
    code, out, _ = _run(["--store", store_path, "list-hosts", "--json"], capsys)
    assert code == 0
    data = json.loads(out)
    assert len(data) == 1
    assert data[0]["name"] == "cp"
    assert data[0]["address"] == "10.0.0.1"


def test_add_host_validation_error_returns_1(store_path, capsys):
    code, _, err = _run(["--store", store_path, "add-host",
                         "--name", "bad name", "--address", "10.0.0.1"], capsys)
    assert code == 1
    assert "error:" in err


def test_add_host_with_unknown_key_errors_1(store_path, capsys):
    code, _, err = _run(["--store", store_path, "add-host",
                         "--name", "cp", "--address", "10.0.0.1",
                         "--key", "does-not-exist"], capsys)
    assert code == 1
    assert "not found" in err


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def test_add_key_from_file_and_list(store_path, tmp_path, capsys):
    key_file = tmp_path / "id.pem"
    key_file.write_text(FAKE_KEY, "utf-8")

    code, out, _ = _run(["--store", store_path, "add-key",
                         "--name", "prod", "--file", str(key_file)], capsys)
    assert code == 0
    assert "SHA256:" in out

    code, out, _ = _run(["--store", store_path, "list-keys"], capsys)
    assert code == 0
    assert "prod" in out
    assert "SHA256:" in out

    # Listed shape never contains the private key body.
    assert "BEGIN OPENSSH PRIVATE KEY" not in out


def test_add_key_then_add_host_with_key_name(store_path, tmp_path, capsys):
    key_file = tmp_path / "id.pem"
    key_file.write_text(FAKE_KEY, "utf-8")
    _run(["--store", store_path, "add-key", "--name", "prod",
          "--file", str(key_file)], capsys)
    code, _, _ = _run(["--store", store_path, "add-host",
                       "--name", "cp", "--address", "10.0.0.1",
                       "--key", "prod"], capsys)
    assert code == 0

    code, out, _ = _run(["--store", store_path, "list-hosts"], capsys)
    assert "key=" in out


def test_add_key_with_missing_file_errors(store_path, capsys):
    code, _, err = _run(["--store", store_path, "add-key",
                         "--name", "x", "--file", "/no/such/file.pem"], capsys)
    assert code == 1
    assert "not found" in err


def test_add_key_with_non_pem_content_errors(store_path, tmp_path, capsys):
    bogus = tmp_path / "bogus.txt"
    bogus.write_text("this is not a key", "utf-8")
    code, _, err = _run(["--store", store_path, "add-key",
                         "--name", "x", "--file", str(bogus)], capsys)
    assert code == 1
    assert "PRIVATE KEY" in err


def test_remove_key_blocked_when_in_use(store_path, tmp_path, capsys):
    key_file = tmp_path / "id.pem"
    key_file.write_text(FAKE_KEY, "utf-8")
    _run(["--store", store_path, "add-key", "--name", "prod",
          "--file", str(key_file)], capsys)
    _run(["--store", store_path, "add-host", "--name", "cp",
          "--address", "10.0.0.1", "--key", "prod"], capsys)
    code, _, err = _run(["--store", store_path, "remove-key", "--name", "prod"], capsys)
    assert code == 1
    assert "in use" in err


# ---------------------------------------------------------------------------
# update / remove
# ---------------------------------------------------------------------------


def test_update_host_partial(store_path, capsys):
    _run(["--store", store_path, "add-host", "--name", "cp",
          "--address", "10.0.0.1"], capsys)
    code, _, _ = _run(["--store", store_path, "update-host",
                       "--name", "cp", "--address", "10.0.0.99"], capsys)
    assert code == 0
    code, out, _ = _run(["--store", store_path, "list-hosts"], capsys)
    assert "10.0.0.99" in out


def test_remove_host_by_name(store_path, capsys):
    _run(["--store", store_path, "add-host", "--name", "cp",
          "--address", "10.0.0.1"], capsys)
    code, _, _ = _run(["--store", store_path, "remove-host", "--name", "cp"], capsys)
    assert code == 0
    code, out, _ = _run(["--store", store_path, "list-hosts"], capsys)
    assert "no hosts" in out


def test_remove_unknown_host_errors(store_path, capsys):
    code, _, err = _run(["--store", store_path, "remove-host", "--name", "nope"], capsys)
    assert code == 1
    assert "not found" in err


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def test_render_prints_ini(store_path, capsys):
    _run(["--store", store_path, "add-host", "--name", "cp",
          "--address", "10.0.0.1", "--group", "control_plane"], capsys)
    code, out, _ = _run(["--store", store_path, "render"], capsys)
    assert code == 0
    assert "[control_plane]" in out
    assert "cp ansible_host=10.0.0.1" in out


def test_render_materialize_writes_run_dir(store_path, tmp_path, capsys):
    key_file = tmp_path / "id.pem"
    key_file.write_text(FAKE_KEY, "utf-8")
    _run(["--store", store_path, "add-key", "--name", "k",
          "--file", str(key_file)], capsys)
    _run(["--store", store_path, "add-host", "--name", "cp",
          "--address", "10.0.0.1", "--key", "k"], capsys)
    target = tmp_path / "run"
    code, out, _ = _run(["--store", store_path, "render",
                         "--materialize", str(target)], capsys)
    assert code == 0
    inv_path = Path(out.strip())
    assert inv_path.exists()
    contents = inv_path.read_text("utf-8")
    assert "cp ansible_host=10.0.0.1" in contents
    # Key file landed next to inventory.ini with 0600.
    keys_dir = inv_path.parent / "keys"
    assert keys_dir.is_dir()
    key_files = list(keys_dir.iterdir())
    assert len(key_files) == 1
    assert key_files[0].stat().st_mode & 0o777 == 0o600
