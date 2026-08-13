# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FH-04 / V23-F — captura de ambiente + benchmark COW rigoroso.

Correção do gate temporal flutuante `COW >= 2×` (Final_Horizon §9):
  - mediana entre repetições INDEPENDENTES;
  - intervalo de confiança (bootstrap não-paramétrico);
  - ambiente e filesystem registrados (kernel, ROCm/driver, thermal, RX 7600);
  - warmup;
  - ORDEM RANDOMIZADA entre os braços (clone vs cow) a cada repetição;
  - integridade conferida após cada run.

IMPORTANTE: o microbenchmark temporal NÃO é gate de corretude. Esta função MEDE; ela nunca falha por
timing. Os gates de corretude do COW continuam no V23-A3.
"""
from __future__ import annotations

import os
import platform
import random
import statistics
import subprocess
import sys
import time

from horizon_memory._engine.horizon_sharded import ShardedWalIndex, copy_stats_wal, shard_of_fact
from horizon_memory._engine.horizon_store import OP_PUT, WalIndex


# --------------------------------------------------------------------------- ambiente
def _run_cmd(args, timeout=20):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _read_file(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return ""


def _parse_rocm_smi():
    """Parseia rocm-smi para nome/driver/temperatura. Nunca falha; campos ausentes ficam vazios."""
    txt = _run_cmd(["rocm-smi", "--showtemp", "--showdriverversion", "--showproductname"])
    info = {"raw_available": bool(txt), "card_series": "", "driver_version": "",
            "gfx_version": "", "temp_edge_c": None, "temp_junction_c": None, "temp_memory_c": None}
    for line in txt.splitlines():
        low = line.lower()
        if "card series" in low:
            info["card_series"] = line.split(":", 1)[-1].strip().strip(": \t")
        elif "driver version" in low:
            info["driver_version"] = line.split(":", 1)[-1].strip()
        elif "gfx version" in low:
            info["gfx_version"] = line.split(":", 1)[-1].strip().strip(": \t")
        elif "sensor edge" in low:
            info["temp_edge_c"] = _last_float(line)
        elif "sensor junction" in low:
            info["temp_junction_c"] = _last_float(line)
        elif "sensor memory" in low:
            info["temp_memory_c"] = _last_float(line)
    return info


def _last_float(line):
    tok = line.replace(":", " ").split()
    for t in reversed(tok):
        try:
            return float(t)
        except ValueError:
            continue
    return None


def _filesystem_type(path="."):
    df = _run_cmd(["df", "-T", path])
    lines = df.splitlines()
    if len(lines) >= 2:
        parts = lines[-1].split()
        if len(parts) >= 2:
            return parts[1]
    return ""


def capture_environment(bench_path="."):
    """Snapshot reproduzível do ambiente. NUNCA inclui segredos/chaves."""
    gpu = _parse_rocm_smi()
    rocm_version = _read_file("/opt/rocm/.info/version")
    drm_uevent = ""
    for card in ("card0", "card1", "card2"):
        u = _read_file(f"/sys/class/drm/{card}/device/uevent")
        if "amdgpu" in u:
            drm_uevent = u
            break
    return {
        "captured_at_unix": int(time.time()),
        "platform": platform.platform(),
        "system": platform.system(),
        "kernel_release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor() or _cpu_model(),
        "cpu_count": os.cpu_count(),
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "filesystem_type": _filesystem_type(bench_path),
        "gpu": {
            "expected": "AMD Radeon RX 7600 (Navi 33 / gfx1102)",
            "detected_series": gpu["card_series"],
            "gfx_version": gpu["gfx_version"],
            "amdgpu_driver_version": gpu["driver_version"],
            "rocm_version": rocm_version,
            "drm_uevent_present": bool(drm_uevent),
            "thermal_state_c": {
                "edge": gpu["temp_edge_c"],
                "junction": gpu["temp_junction_c"],
                "memory": gpu["temp_memory_c"],
            },
            "note": "O microbenchmark COW é CPU/memória (cópia de índice L0); a GPU é registrada para "
                    "reprodutibilidade do host RX 7600, não é o alvo do timing.",
        },
    }


def _cpu_model():
    txt = _read_file("/proc/cpuinfo")
    for line in txt.splitlines():
        if "model name" in line:
            return line.split(":", 1)[-1].strip()
    return ""


# --------------------------------------------------------------------------- estatística
def _bootstrap_ci(samples, confidence=0.95, resamples=2000, seed=1):
    """IC bootstrap não-paramétrico da MEDIANA. Determinístico por seed."""
    if len(samples) < 2:
        v = samples[0] if samples else float("nan")
        return (v, v)
    rng = random.Random(seed)
    meds = []
    n = len(samples)
    for _ in range(resamples):
        draw = [samples[rng.randrange(n)] for _ in range(n)]
        meds.append(statistics.median(draw))
    meds.sort()
    lo_i = int((1 - confidence) / 2 * resamples)
    hi_i = int((1 + confidence) / 2 * resamples) - 1
    return (meds[lo_i], meds[hi_i])


# --------------------------------------------------------------------------- COW benchmark
def _bits(shards):
    return max(1, (shards - 1).bit_length())


def _build_clone(l0):
    idx = WalIndex()
    for f in range(1, l0 + 1):
        idx.apply(f, 1, OP_PUT, f & 0xFF)
    return idx


def _build_cow(l0, shards):
    idx = ShardedWalIndex.empty(_bits(shards))
    b = idx.begin_mutation()
    for f in range(1, l0 + 1):
        b.apply(f, 1, OP_PUT, f & 0xFF)
    return b.freeze()


def _measure_clone(idx, batch_fids):
    t0 = time.perf_counter()
    b = idx.begin_mutation()
    for f in batch_fids:
        b.apply(f, 2, OP_PUT, (f + 1) & 0xFF)
    frozen = b.freeze()
    dt = time.perf_counter() - t0
    # integridade: o batch aplicou os novos valores
    ok = all((frozen.get(f) or (0,0,-1))[2] == ((f + 1) & 0xFF) for f in set(batch_fids))
    return dt, ok


def _measure_cow(idx, batch_fids):
    t0 = time.perf_counter()
    b = idx.begin_mutation()
    for f in batch_fids:
        b.apply(f, 2, OP_PUT, (f + 1) & 0xFF)
    cs = copy_stats_wal(b)
    frozen = b.freeze()
    dt = time.perf_counter() - t0
    ok = all((frozen.get(f) or (0,0,-1))[2] == ((f + 1) & 0xFF) for f in set(batch_fids))
    return dt, ok, cs.entries_copied, cs.shards_touched


def cow_benchmark(l0_sizes, batches, shard_counts, *, repetitions=15, warmup=3, seed=1):
    """Mede clone vs COW com repetições independentes, warmup, ordem randomizada e integridade.
    Retorna linhas com mediana + IC do speedup. NÃO decide corretude por timing."""
    rng = random.Random(seed)
    rows = []
    for l0 in l0_sizes:
        for batch in batches:
            batch_fids = [rng.randint(1, l0) for _ in range(batch)]
            for shards in shard_counts:
                clone_samples, cow_samples, speedups = [], [], []
                entries_copied = touched = 0
                integrity_ok = True
                total = warmup + repetitions
                for i in range(total):
                    # ordem randomizada dos braços a cada repetição
                    arms = ["clone", "cow"]
                    rng.shuffle(arms)
                    ct = wt = None
                    for arm in arms:
                        if arm == "clone":
                            ci = _build_clone(l0)
                            ct, cok = _measure_clone(ci, batch_fids)
                            integrity_ok &= cok
                        else:
                            si = _build_cow(l0, shards)
                            wt, wok, ec, th = _measure_cow(si, batch_fids)
                            integrity_ok &= wok
                            entries_copied, touched = ec, th
                    if i >= warmup:                       # descarta warmup
                        clone_samples.append(ct)
                        cow_samples.append(wt)
                        speedups.append(ct / wt if wt > 0 else float("inf"))
                med_clone = statistics.median(clone_samples)
                med_cow = statistics.median(cow_samples)
                med_speedup = statistics.median(speedups)
                ci_lo, ci_hi = _bootstrap_ci(speedups, seed=seed + l0 + batch + shards)
                rows.append({
                    "l0": l0, "batch": batch, "shards": shards,
                    "repetitions": repetitions, "warmup": warmup,
                    "clone_us_median": round(med_clone * 1e6, 2),
                    "cow_us_median": round(med_cow * 1e6, 2),
                    "speedup_median_x": round(med_speedup, 3),
                    "speedup_ci95": [round(ci_lo, 3), round(ci_hi, 3)],
                    "cow_entries_copied": entries_copied,
                    "cow_shards_touched": touched,
                    "integrity_ok": bool(integrity_ok),
                })
    return rows
