"""corthexis.profiles — recommended memory profile on simulated hardware, detection helpers."""
import json
import time
from pathlib import Path

import pytest

from corthexis import profiles

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("cores,ram,acc,expected", [
    (2, 2.0, None, "leger"),                                        # small VM
    (4, 3.8, None, "leger"),                                        # 4 GB VM
    (8, 12.6, None, "leger"),                                       # 8 cores but 12 GB
    (8, 15.5, None, "standard"),                                    # 8 cores / 16 GB
    (36, 62.5, None, "standard"),                                   # big CPU box
    (8, 32, {"kind": "cuda", "name": "RTX 3090", "vram_gb": 24.0}, "gpu"),
    (8, 32, {"kind": "cuda", "name": "GT 1030", "vram_gb": 2.0}, "standard"),
    (4, 8, {"kind": "cuda", "name": "GT 1030", "vram_gb": 2.0}, "leger"),
    (36, 62.5, {"kind": "sycl", "name": "Intel(R) Arc(TM) Pro B60 Graphics",
                "vram_gb": 22.7}, "gpu"),
    (8, 16, {"kind": "sycl", "name": "Intel discrete GPU 0xe211", "vram_gb": None}, "gpu"),
    (10, 16, {"kind": "metal", "name": "Apple M2", "vram_gb": 12.0}, "gpu"),
    (8, 8, {"kind": "metal", "name": "Apple M1", "vram_gb": 6.0}, "gpu"),
])
def test_recommendation(cores, ram, acc, expected):
    rec = profiles.recommend(cores, ram, acc)
    assert rec["recommended"] == expected
    assert rec["schema"] == "corthexis/memory-profile/v1"
    fits = {p["id"]: p["fits"] for p in rec["profiles"]}
    assert fits["leger"] and fits[expected]
    assert rec["accel"] == (acc["kind"] if expected == "gpu" else "cpu")


def test_warnings_and_reasons():
    rec = profiles.recommend(2, 2.0)
    assert any("minimum" in w for w in rec["warnings"])
    assert any("slow" in w for w in rec["warnings"])
    rec = profiles.recommend(4, 8, {"kind": "cuda", "name": "GT 1030", "vram_gb": 2.0})
    assert "under 4 GB" in rec["reason"]
    rec = profiles.recommend(10, 16, {"kind": "metal", "name": "Apple M2", "vram_gb": 12.0})
    assert rec["hardware"]["accelerator"]["serve"] == "native"
    assert any("natively" in w for w in rec["warnings"])


def test_profile_table_costs():
    t = profiles.PROFILES
    assert t["leger"]["reranker"] is None and t["leger"]["rerank_policy"] == "off"
    assert t["standard"]["rerank_policy"] == "async"
    assert t["gpu"]["reranker"] == "qwen3-reranker-0.6b-q8"
    for p in profiles.ORDER:
        c = t[p]["costs"]
        for k in ("ram_gb", "query_ms_p50", "reindex_250k_h", "mrr", "download_mb"):
            assert c[k] is not None
    assert t["gpu"]["costs"]["mrr"] > t["standard"]["costs"]["mrr"] >= t["leger"]["costs"]["mrr"]


def test_env_lines():
    rec = profiles.recommend(8, 16)
    assert profiles.env_lines(rec) == ("CORTHEXIS_MEMORY_PROFILE=standard\n"
                                       "CORTHEXIS_EMBED_ACCEL=cpu\n")
    assert profiles.env_lines(rec, "SOKKAN").startswith("SOKKAN_MEMORY_PROFILE=standard")


def test_intel_sysfs_detection(tmp_path):
    def dev(name, vendor, device, cls):
        d = tmp_path / name
        d.mkdir()
        (d / "vendor").write_text(vendor + "\n")
        (d / "device").write_text(device + "\n")
        (d / "class").write_text(cls + "\n")
    dev("0000:00:02.0", "0x8086", "0x9a49", "0x030000")   # integrated Iris Xe: ignored
    dev("0000:19:00.0", "0x8086", "0xe211", "0x030000")   # Arc Pro B60
    dev("0000:20:00.0", "0x10de", "0x2204", "0x030000")   # NVIDIA: not Intel
    found = profiles._intel_sysfs(str(tmp_path))
    assert found == [{"name": "Intel discrete GPU 0xe211", "vram_gb": None}]


def test_intel_clinfo_parsing(monkeypatch):
    out = """Number of platforms 1
  Device Name                                     Intel(R) Arc(TM) Pro B60 Graphics
  Global memory size                              24385683456 (22.71GiB)
  Device Name                                     Intel(R) UHD Graphics 770
  Global memory size                              13000000000 (12.1GiB)
"""
    monkeypatch.setattr(profiles.shutil, "which", lambda b: "/usr/bin/" + b)
    monkeypatch.setattr(profiles, "_run", lambda cmd: out)
    assert profiles._intel_clinfo() == [
        {"name": "Intel(R) Arc(TM) Pro B60 Graphics", "vram_gb": 22.7}]


def test_nvidia_parsing_picks_largest(monkeypatch):
    monkeypatch.setattr(profiles.shutil, "which", lambda b: "/usr/bin/" + b)
    monkeypatch.setattr(profiles, "_run", lambda cmd: "RTX 2080 Ti, 11264\nRTX 3090, 24576")
    assert profiles._nvidia() == {"kind": "cuda", "name": "RTX 3090", "vram_gb": 24.0}


def test_detect_runs_here():
    rec = profiles.detect()
    assert rec["recommended"] in profiles.ORDER
