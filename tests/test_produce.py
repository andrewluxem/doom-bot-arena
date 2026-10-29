"""Milestone 3: the production runner's rotation, seeds, IWAD gate, upload order, index and pruning (no network)."""

import datetime as dt
import json
import os

import pytest

from tools import blob, produce

CFG = {"maps": [{"map": "MAP01", "iwad": "freedoom2"}, {"map": "MAP10", "iwad": "freedoom2"},
                {"map": "E1M5", "iwad": "freedoom1"}, {"map": "E1M1", "iwad": "/nonexistent/doom.wad", "if_present": True}],
       "duration_tics": 10500, "crf": 32, "keep_local": 2, "min_free_gb": 20, "freedoom_sha256": []}


class FakeStore:
    """Blob's put, head, list and delete, in memory; puts are logged in order."""

    def __init__(self, files=None):
        self.files = dict(files or {})  # pathname -> (bytes, max_age)
        self.puts = []

    def url(self, pathname):
        return f"https://abc.public.blob.vercel-storage.com/{pathname}"

    def put(self, pathname, data, ctype=None, overwrite=False, max_age=None):
        if pathname in self.files and not overwrite:
            raise blob.BlobError(400, "bad_request", "This blob already exists, use `allowOverwrite: true`")
        self.files[pathname] = (bytes(data), max_age)
        self.puts.append(pathname)
        return {"url": self.url(pathname), "pathname": pathname}

    def head(self, pathname):
        if pathname not in self.files:
            return None
        return {"url": self.url(pathname), "size": len(self.files[pathname][0])}

    def list(self, prefix):
        return [{"pathname": p, "size": len(d), "url": self.url(p)} for p, (d, _) in self.files.items() if p.startswith(prefix)]


def match_json(bid, slots=8, sha="f" * 64, date="2026-10-29T09:20:00Z"):
    return {"id": bid, "map": bid.split("-")[1], "date": date, "duration_tics": 10500, "ticrate": 35, "schema_version": 5,
            "iwad": {"name": "freedoom2.wad", "sha256": sha},
            "bots": [{"slot": s, "code": f"B{s}", "name": f"Bot {s}"} for s in range(1, slots + 1)],
            "score": {"rows": [{"slot": s, "displayed_frags": s % 4, "displayed_deaths": s} for s in range(1, slots + 1)]},
            "pov": [{"slot": s, "path": f"pov/slot{s}.mp4", "sha256": "0" * 64} for s in range(1, slots + 1)]}


def write_bundle(root, bid, **kw):
    d = root / bid
    (d / "pov").mkdir(parents=True)
    m = match_json(bid, **kw)
    (d / "match.json").write_text(json.dumps(m))
    for name in produce.DATA_FILES:
        (d / name).write_text(name)
    for v in m["pov"]:
        (d / v["path"]).write_bytes(b"v" * v["slot"])
    return d


@pytest.fixture
def no_vizdoom(monkeypatch):
    monkeypatch.setattr(produce, "freedoom_hashes", lambda cfg: {"f" * 64} | set(cfg.get("freedoom_sha256") or []))
    monkeypatch.setattr(produce, "validate", lambda d: None)
    monkeypatch.setattr(produce, "fetch_json", lambda url: None)


# ---------------------------------------------------------------- rotation and seeds
def test_rotation_follows_the_newest_published_map_and_skips_absent_iwads():
    index = lambda m: {"bundles": [{"id": f"20261029T000000Z-{m}-s1", "map": m}]}  # noqa: E731
    assert produce.next_map(CFG, {"bundles": []})["map"] == "MAP01"
    assert produce.next_map(CFG, index("MAP01"))["map"] == "MAP10"
    assert produce.next_map(CFG, index("MAP10"))["map"] == "E1M5"
    assert produce.next_map(CFG, index("E1M5"))["map"] == "MAP01"  # E1M1's doom.wad is not present
    assert produce.next_map(CFG, index("MAP07"))["map"] == "MAP01"


def test_seed_is_the_date_and_the_days_sequence():
    today = dt.datetime(2026, 10, 29, 9, 0, tzinfo=dt.timezone.utc)
    index = {"bundles": [{"id": "20261029T032000Z-MAP01-s2026102901"}, {"id": "20261028T192000Z-E1M5-s2026102803"}]}
    assert produce.day_seed({"bundles": []}, today) == 2026102901
    assert produce.day_seed(index, today) == 2026102902
    assert produce.day_seed(index, today) < 2**31


# ---------------------------------------------------------------- IWAD gate
def test_non_freedoom_iwad_is_refused_without_the_allow_flag(no_vizdoom):
    produce.check_freedoom("f" * 64, "freedoom2.wad", CFG, False)
    with pytest.raises(produce.Failure, match="not a known Freedoom release"):
        produce.check_freedoom("d" * 64, "doom.wad", CFG, False)
    produce.check_freedoom("d" * 64, "doom.wad", CFG, True)
    produce.check_freedoom("d" * 64, "doom.wad", {**CFG, "allow_non_freedoom": True}, False)
    produce.check_freedoom("d" * 64, "freedoom-0.14.wad", {**CFG, "freedoom_sha256": ["d" * 64]}, False)


def test_publish_refuses_a_non_freedoom_bundle_before_any_upload(tmp_path, no_vizdoom):
    store = FakeStore({"index.json": (b'{"bundles": []}', 60)})
    d = write_bundle(tmp_path, "20261029T092000Z-E1M1-s2026102901", sha="d" * 64)
    with pytest.raises(produce.Failure, match="refusing to publish"):
        produce.publish(store, None, {}, {**CFG, "out_dir": str(tmp_path)}, str(d), False)
    assert store.puts == []


# ---------------------------------------------------------------- upload and index
def test_bundle_uploads_never_overwrite_and_index_goes_last(tmp_path, no_vizdoom, monkeypatch):
    env_path = tmp_path / "env"
    env_path.write_text("BLOB_READ_WRITE_TOKEN='x'\n")
    store = FakeStore()
    monkeypatch.setattr(produce, "load_index", lambda s, p: {"bundles": [{"id": "old", "date": "2026-10-28T00:00:00Z"}]})
    bid = "20261029T092000Z-MAP10-s2026102901"
    entry = produce.publish(store, str(env_path), {}, {**CFG, "out_dir": str(tmp_path)}, str(write_bundle(tmp_path, bid)), False)
    prefix = f"bundles/{bid}/"
    assert store.puts[-2:] == [prefix + "match.json", "index.json"]
    assert store.puts[:8] == [prefix + f"pov/slot{s}.mp4" for s in range(1, 9)]
    assert all(store.files[p][1] is None for p in store.puts[:-1])  # bundle files keep the long default cache
    assert store.files["index.json"][1] == 60
    index = json.loads(store.files["index.json"][0])
    assert [b["id"] for b in index["bundles"]] == [bid, "old"]
    assert entry["path"] == prefix and entry["standings"][0]["frags"] == 3
    assert blob.read_env(env_path)["BLOB_PUBLIC_BASE"] == "https://abc.public.blob.vercel-storage.com"


def test_a_stale_remote_index_does_not_drop_the_entry_this_box_last_wrote(tmp_path, monkeypatch):
    old = {"id": "20261028T192000Z-E1M5-s1", "date": "2026-10-28T19:20:00Z"}
    new = {"id": "20261029T032000Z-MAP01-s2", "date": "2026-10-29T03:20:00Z"}
    store = FakeStore({"index.json": (json.dumps({"bundles": [old]}).encode(), 60)})  # the CDN's previous version
    monkeypatch.setattr(produce, "fetch_json", lambda url: json.loads(store.files["index.json"][0]))
    (tmp_path / "index.json").write_text(json.dumps({"bundles": [new, old]}))
    assert [e["id"] for e in produce.load_index(store, str(tmp_path / "index.json"))["bundles"]] == [new["id"], old["id"]]
    assert produce.load_index(store, str(tmp_path / "none.json"))["bundles"] == [old]


def test_a_resumed_upload_keeps_equal_files_and_refuses_different_ones(tmp_path, no_vizdoom):
    bid = "20261029T092000Z-MAP10-s2026102901"
    d = write_bundle(tmp_path, bid)
    m = json.loads((d / "match.json").read_text())
    store = FakeStore({f"bundles/{bid}/pov/slot1.mp4": (b"v", None)})
    produce.upload_bundle(store, str(d), m)
    assert f"bundles/{bid}/pov/slot1.mp4" not in store.puts
    store = FakeStore({f"bundles/{bid}/pov/slot1.mp4": (b"other", None)})
    with pytest.raises(produce.Failure, match="never overwritten"):
        produce.upload_bundle(store, str(d), m)


def test_index_entry_standings_rank_by_frags_then_deaths():
    e = produce.index_entry(match_json("20261029T092000Z-MAP01-s1"), 123)
    assert [(s["slot"], s["frags"]) for s in e["standings"][:3]] == [(3, 3), (7, 3), (2, 2)]
    assert e["bytes"] == 123 and e["iwad"] == "freedoom2.wad"


# ---------------------------------------------------------------- failure and pruning
def test_a_failed_run_records_status_and_leaves_index_alone(tmp_path, monkeypatch):
    cfg_path = tmp_path / "production.yaml"
    cfg_path.write_text(json.dumps({**CFG, "out_dir": str(tmp_path / "out")}))
    env_path = tmp_path / "env"
    env_path.write_text('BLOB_READ_WRITE_TOKEN="vercel_blob_rw_abc_secret"\n')
    store = FakeStore({"index.json": (b'{"bundles": []}', 60)})
    monkeypatch.setattr(produce.blob, "Blob", lambda token, store_id=None: store)
    monkeypatch.setattr(produce, "fetch_json", lambda url: json.loads(store.files[url.split(".com/")[1]][0]))
    monkeypatch.setattr(produce, "free_gb", lambda p: 100.0)
    for n in (1, 2):
        code = produce.main(["--config", str(cfg_path), "--env", str(env_path), "run", "--iwad", "/missing.wad"])
        assert code == 1
        status = json.loads(store.files["status.json"][0])
        assert status["consecutive_failures"] == n and "IWAD not found" in status["last_error"]
    assert store.files["index.json"][0] == b'{"bundles": []}' and "index.json" not in store.puts


def test_disk_check_refuses_under_the_minimum(tmp_path, monkeypatch):
    monkeypatch.setattr(produce, "free_gb", lambda p: 12.5)

    class Args:
        status = {}
    with pytest.raises(produce.Failure, match="12.5 GB free"):
        produce.run(Args, {**CFG, "out_dir": str(tmp_path)}, {}, FakeStore())


def test_prune_keeps_the_newest_bundles_and_captures(tmp_path):
    ids = [f"20261029T0{h}2000Z-MAP01-s{h}" for h in range(4)]
    for bid in ids:
        (tmp_path / bid).mkdir()
        (tmp_path / "raw" / ".capture" / bid).mkdir(parents=True)
    (tmp_path / "raw" / ids[3]).mkdir()  # a raw copy left by an interrupted re-encode
    (tmp_path / "status.json").write_text("{}")
    removed = produce.prune({"out_dir": str(tmp_path), "keep_local": 2})
    assert removed == ids[:2]
    assert sorted(os.listdir(tmp_path)) == sorted(ids[2:] + ["raw", "status.json"])
    assert sorted(os.listdir(tmp_path / "raw")) == [".capture"]
    assert sorted(os.listdir(tmp_path / "raw" / ".capture")) == ids[2:]


def test_env_file_quotes_and_in_place_update(tmp_path):
    p = tmp_path / "env"
    p.write_text("# blob\nBLOB_READ_WRITE_TOKEN='a=b'\nexport BLOB_STORE_ID=\"store_x\"\n")
    os.chmod(p, 0o600)
    assert blob.read_env(p) == {"BLOB_READ_WRITE_TOKEN": "a=b", "BLOB_STORE_ID": "store_x"}
    blob.set_env(p, "BLOB_PUBLIC_BASE", "https://h.public.blob.vercel-storage.com")
    blob.set_env(p, "BLOB_PUBLIC_BASE", "https://h.public.blob.vercel-storage.com")
    assert blob.read_env(p)["BLOB_PUBLIC_BASE"] == "https://h.public.blob.vercel-storage.com"
    assert p.read_text().count("BLOB_PUBLIC_BASE") == 1 and p.read_text().startswith("# blob\n")
    assert os.stat(p).st_mode & 0o777 == 0o600
