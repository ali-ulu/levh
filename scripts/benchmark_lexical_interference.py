"""Benchmark the model-free interference scan (hash embedder).

Why this exists
---------------
``MemoryWriteMixin._interference_candidates`` cannot use cosine proximity in
model-free mode: the ``hash`` embedder is positional, so it ranks the wrong
neighbours. The fallback scores the new memory against *every* other memory in
the same project with ``lexical.mutual_similarity``. Each of those calls runs
``min(similarity(a, b), similarity(b, a))``, and every ``similarity`` call
tokenizes both texts again — so admitting one memory into a store of N rows
costs about ``4 * N`` tokenizations, and importing M rows costs about
``4 * N * M``. That is the O(N^2) ingestion slowdown this harness measures.

What it reports, per corpus size N
----------------------------------
* **A) end-to-end ``store()``** — a batch of sequential stores (the bulk-import
  shape) and one more store after it, the steady-state per-item cost. Includes
  the SQLite write and the embedding, so on a loaded machine it is noisy; the
  tokenization count beside it is not.
* **B) the interference scan alone** — ``_interference_candidates`` called
  without any database write. This is the path the cache optimises, so it is
  the number that shows the optimisation directly.
* ``tokenizations`` — the number of ``re`` scans actually performed. Counted by
  wrapping ``lexical``'s word pattern, so it is deterministic and free of
  scheduler noise; it is the primary evidence.

It never touches the live store: the engine is built on a throwaway database
under the system temp directory.

Run from the repository root:

    .venv\\Scripts\\python.exe scripts/benchmark_lexical_interference.py
    .venv\\Scripts\\python.exe scripts/benchmark_lexical_interference.py --sizes 200,1000 --batch 25
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import sys
import tempfile
from pathlib import Path
from time import perf_counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.core import lexical
from server.core.lexical import mutual_similarity
from server.core.memory_engine import MemoryEngine
from server.core.types import Memory, MemoryType

# Seed corpus vocabulary: commit-message-like text, the shape the git import
# actually stores. Seeds are never compared with each other, so overlap between
# them is irrelevant.
_SEED_WORDS = (
    "branch deploy pipeline release rollback schema migration database index "
    "latency throughput cache invalidation parser serializer adapter retry "
    "timeout backoff circuit breaker queue worker scheduler cron webhook "
    "payload envelope checksum signature token session cookie header route "
    "middleware handler controller service repository transaction isolation "
    "lock deadlock replica failover shard partition bucket object storage "
    "artifact bundle manifest checksum version changelog regression snapshot "
    "trace span metric histogram alert threshold dashboard incident postmortem "
    "runbook playbook rollout canary blue green feature flag experiment cohort "
    "audit retention archival purge compaction vacuum analyze query planner "
    "connection pool socket buffer limit quota throttle lease heartbeat "
    "leader election consensus quorum log replication offset commit consumer "
    "producer topic stream batch window aggregation watermark checkpoint"
).split()

# Probe vocabulary: disjoint 6-word blocks so two probes share no content word
# at all. That keeps the measured batch free of interference writes (which
# would add SQLite time on top of the scan being measured) and keeps the
# tokenization count a pure function of the corpus size.
_PROBE_WORDS = (
    "aardvark abacus abalone abandon abbey abdomen abduct abelia benign "
    "bicycle biscuit blizzard blossom blueprint bonanza bracket bravado "
    "cactus calypso caravan carnation cascade cathedral cavern ceramic "
    "daffodil dalmation dandelion darling decanter delta denture dialect "
    "echo eclipse eggplant elixir embargo emerald enclave endeavour "
    "falcon fandango fennel ferret fiddler flagship flotilla fuchsia "
    "gadget gallop gardenia gazelle gemstone geyser gingerbread glacier "
    "hammock harbour harpsichord hazelnut hedgehog helicopter hibiscus "
    "iceberg igloo illusion impala incisor indigo ingot iris ivy ivory "
    "jackal jamboree jasmine jetty jigsaw jonquil jubilee juniper "
    "kaleidoscope kangaroo kayak kestrel kettle kimono kiosk kitten "
    "labyrinth lagoon lantern lasagna lavender lemur lichen lilac lobster "
    "macaw magenta magnolia mahogany mallard mandolin mangrove marigold "
    "nectarine nightingale nimbus nutmeg oasis obelisk obsidian octagon "
    "paladin panorama papaya papyrus paragon parchment pavilion pelican "
    "quagmire quail quantum quarry quicksilver quiver quokka quorum "
    "raccoon radish rampart raspberry rattan ravioli reindeer rhubarb "
    "saffron salamander sandalwood sapphire saxophone scallop seahorse "
    "tabby tabernacle taffeta talisman tamarind tangerine tapestry teak "
    "ukulele umbrella unicorn urchin utopia valance valentine vanilla "
    "wagon walrus wampum wasabi watercress waxwing weathervane whippet "
    "xylophone yacht yam yardstick yarrow yeoman yodel yucca zebra zenith "
    "zephyr zeppelin ziggurat zinc zinnia zircon zither zodiac zucchini"
).split()


class _CountingPattern:
    """A ``re.Pattern`` proxy that counts the texts actually scanned.

    ``lexical`` resolves ``_WORD`` from the module globals on every call, so
    swapping the attribute is enough to observe real tokenization work without
    changing the code under test.
    """

    def __init__(self, pattern) -> None:
        self._pattern = pattern
        self.calls = 0

    def findall(self, text: str) -> list[str]:
        self.calls += 1
        return self._pattern.findall(text)


def _install_token_counter() -> _CountingPattern:
    counter = _CountingPattern(lexical._WORD)  # type: ignore[attr-defined]
    lexical._WORD = counter  # type: ignore[attr-defined]
    return counter


def _clear_terms_cache() -> None:
    """Drop the token cache between phases so each phase measures from cold.

    Absent before the optimisation (which is the point of the comparison), so
    the lookup is defensive.
    """
    cached = getattr(lexical, "_content_terms", None)
    clear = getattr(cached, "cache_clear", None)
    if callable(clear):
        clear()


def _seed_content(rng: random.Random) -> str:
    return " ".join(rng.choice(_SEED_WORDS) for _ in range(14))


def _probe_contents(count: int) -> list[str]:
    """Probe texts built from disjoint 6-word blocks of the probe vocabulary."""
    blocks = [x for x in _PROBE_WORDS if len(x) >= 3 and x not in lexical._STOPWORDS]
    if count * 6 > len(blocks):
        raise SystemExit(
            f"batch {count} needs {count * 6} probe words, vocabulary has {len(blocks)}"
        )
    return [
        " ".join(blocks[i * 6 : (i + 1) * 6]) for i in range(count)
    ]


def _stem_collisions(left: tuple[str, ...], right: tuple[str, ...]) -> list[tuple[str, str]]:
    """Pairs that ``_stem_matches`` would treat as one retrieval term.

    Used to prove the probe corpora are lexically disjoint from each other and
    from the seed corpus, so the timed batch triggers no interference writes.
    """
    return [
        (a, b)
        for a in left
        for b in right
        if lexical._stem_matches(a, b) or lexical._stem_matches(b, a)
    ]


async def _seed(engine: MemoryEngine, size: int, write_db: bool = True) -> None:
    """Fill the corpus without running the interference scan.

    Going through ``store()`` here would itself be the O(N^2) path this harness
    measures, so the rows are written the way ``_store`` writes them and the
    interference pass is simply skipped. ``write_db=False`` keeps the corpus in
    the vector store only, for the scan-only measurement.
    """
    rng = random.Random(20261003)
    for _ in range(size):
        content = _seed_content(rng)
        mem = Memory(
            content=content,
            embedding=await engine.embedder.embed(content),
            memory_type=MemoryType.EPISODIC,
            project="bench",
        )
        mem.valid_from = mem.created_at
        if write_db:
            await engine.episodic.store(mem)
        engine.vector_store.add(mem)


def _candidate_for(new_memory: Memory):
    """The same predicate ``_apply_interference`` builds for the scan."""

    def _candidate(m: Memory) -> bool:
        return (
            m.id != new_memory.id
            and not m.pinned
            and m.workspace_id == new_memory.workspace_id
            and m.project == new_memory.project
        )

    return _candidate


async def _measure_scan(size: int, batch: int) -> dict:
    """Time ``_interference_candidates`` alone — the path the cache optimises.

    No SQLite and no episodic write, so the number is the scan's own cost: one
    tokenization per candidate before the cache, one dictionary lookup per
    candidate after it.
    """
    tmpdir = tempfile.mkdtemp(prefix="levh-bench-scan-")
    engine = MemoryEngine(db_path=str(Path(tmpdir) / "scan.db"), embedder_mode="hash")
    await engine.initialize()
    try:
        await _seed(engine, size, write_db=False)
        content = _probe_contents(1)[0]
        new_memory = Memory(
            content=content,
            embedding=await engine.embedder.embed(content),
            memory_type=MemoryType.EPISODIC,
            project="bench",
        )
        candidate = _candidate_for(new_memory)

        _clear_terms_cache()
        counter = _install_token_counter()

        # The first scan pays for whatever tokenization the corpus still needs.
        first_before = counter.calls
        started = perf_counter()
        engine._interference_candidates(new_memory, candidate)
        first_seconds = perf_counter() - started
        first_tokenizations = counter.calls - first_before

        # Everything after is the steady state a bulk import runs in.
        before = counter.calls
        durations: list[float] = []
        for _ in range(batch):
            started = perf_counter()
            engine._interference_candidates(new_memory, candidate)
            durations.append(perf_counter() - started)

        return {
            "size": size,
            "batch": batch,
            "first_ms": first_seconds * 1000,
            "first_tokenizations": first_tokenizations,
            "min_ms": min(durations) * 1000,
            "median_ms": statistics.median(durations) * 1000,
            "tokenizations": counter.calls - before,
            "tokenizations_per_scan": (counter.calls - before) / batch,
        }
    finally:
        await engine.shutdown()


async def _measure(size: int, batch: int) -> dict:
    tmpdir = tempfile.mkdtemp(prefix="levh-bench-")
    engine = MemoryEngine(
        db_path=str(Path(tmpdir) / "bench.db"), embedder_mode="hash", short_term_max=50
    )
    await engine.initialize()
    try:
        await _seed(engine, size)
        probes = _probe_contents(batch + 1)

        # A probe must not reach the lexical interference floor, or the timing
        # would include SQLite weaken/retire writes on top of the scan.
        worst = max(
            mutual_similarity(a, b) for i, a in enumerate(probes) for b in probes[i + 1 :]
        )
        _clear_terms_cache()
        counter = _install_token_counter()
        before = counter.calls
        durations: list[float] = []
        for content in probes[:batch]:
            started = perf_counter()
            await engine.store(content=content, project="bench", memory_type="episodic")
            durations.append(perf_counter() - started)
        batch_tokenizations = counter.calls - before

        # One more store with the corpus as warm as the batch left it: the
        # steady-state per-item cost during a long import.
        warm_before = counter.calls
        warm_started = perf_counter()
        await engine.store(content=probes[batch], project="bench", memory_type="episodic")
        warm_seconds = perf_counter() - warm_started
        warm_tokenizations = counter.calls - warm_before

        return {
            "size": size,
            "batch": batch,
            "worst_probe_similarity": worst,
            "batch_total_s": sum(durations),
            "batch_min_ms": min(durations) * 1000,
            "batch_median_ms": statistics.median(durations) * 1000,
            "batch_tokenizations": batch_tokenizations,
            "batch_tokenizations_per_item": batch_tokenizations / batch,
            "warm_ms": warm_seconds * 1000,
            "warm_tokenizations": warm_tokenizations,
        }
    finally:
        await engine.shutdown()


async def _main(args: argparse.Namespace) -> int:
    sizes = [int(part) for part in args.sizes.split(",") if part.strip()]
    probe_words = tuple(
        word for word in _PROBE_WORDS[: (args.batch + 1) * 6] if len(word) >= 3
    )
    cross = _stem_collisions(_SEED_WORDS, probe_words)
    within = {
        tuple(sorted(pair))
        for pair in _stem_collisions(probe_words, probe_words)
        if pair[0] != pair[1]
    }
    print(f"levh model-free interference benchmark - hash embedder, batch={args.batch}")
    print("tokenizations = number of re scans of a text (counted, not estimated)")
    print(
        "lexical disjointness: "
        f"seed/probe stem collisions={len(cross)}, probe/probe={len(within)} "
        "(0 means the timed batch cannot trigger interference writes)\n"
    )
    print("A) end-to-end store() - includes the SQLite write and the embedding")
    header_a = (
        f"{'N':>6} {'batch_tok':>12} {'tok/item':>10} {'batch_s':>9} "
        f"{'min_ms':>8} {'med_ms':>8} {'warm_ms':>9} {'warm_tok':>9}"
    )
    print(header_a)
    print("-" * len(header_a))
    rows = []
    for size in sizes:
        row = await _measure(size, args.batch)
        rows.append(row)
        print(
            f"{row['size']:>6} {row['batch_tokenizations']:>12} "
            f"{row['batch_tokenizations_per_item']:>10.1f} "
            f"{row['batch_total_s']:>9.3f} {row['batch_min_ms']:>8.2f} "
            f"{row['batch_median_ms']:>8.2f} {row['warm_ms']:>9.2f} "
            f"{row['warm_tokenizations']:>9}"
        )

    print("\nB) interference scan alone - no SQLite, no episodic write")
    header_b = (
        f"{'N':>6} {'first_ms':>9} {'first_tok':>10} {'min_ms':>8} "
        f"{'med_ms':>8} {'tok/scan':>9}"
    )
    print(header_b)
    print("-" * len(header_b))
    scan_rows = []
    for size in sizes:
        row = await _measure_scan(size, args.batch)
        scan_rows.append(row)
        print(
            f"{row['size']:>6} {row['first_ms']:>9.2f} "
            f"{row['first_tokenizations']:>10} {row['min_ms']:>8.2f} "
            f"{row['median_ms']:>8.2f} {row['tokenizations_per_scan']:>9.1f}"
        )

    print()
    for row in rows:
        print(
            f"N={row['size']}: max pairwise probe similarity "
            f"{row['worst_probe_similarity']:.3f} (< 0.65 means no interference "
            f"writes entered the timing)"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sizes", default="200,1000", help="corpus sizes to measure (comma separated)"
    )
    parser.add_argument(
        "--batch", type=int, default=25, help="sequential stores timed per corpus size"
    )
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
