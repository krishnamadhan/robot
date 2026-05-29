"""
tools/llm_bakeoff.py — Reusable LLM comparison harness for Cosmo.

Runs a fixture suite against multiple models, measures latency + quality,
and writes a markdown report. Re-run any time you want to compare models.

Usage:
  python3 tools/llm_bakeoff.py --suite tests/bakeoff/prompts/ --output docs/LLM_BAKEOFF_REPORT.md
  python3 tools/llm_bakeoff.py --suite tests/bakeoff/prompts/ --models ollama/llama3.2:1b claude
  python3 tools/llm_bakeoff.py --list-fixtures   # show available prompts
  python3 tools/llm_bakeoff.py --dry-run         # validate fixtures, no API calls

RAM gate for 3b model:
  Script checks free RAM before loading 3b. If available RAM (with services
  running) is < RAM_NEEDED_3B_MB, 3b is skipped and noted in the report.
  Run with cosmo + banteragent active for a realistic reading.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Config ────────────────────────────────────────────────────────────────────

OLLAMA_URL       = os.environ.get("OLLAMA_URL", "http://localhost:11434")
ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
MAX_CALLS_PER_MODEL = 20          # hard cap per model — protects Cosmo's token budget
RAM_NEEDED_3B_MB = 900            # minimum free MB needed to load llama3.2:3b safely
CLAUDE_MODEL     = "claude-haiku-4-5-20251001"

DEFAULT_MODELS = [
    "ollama/llama3.2:1b",
    "ollama/llama3.2:3b",   # will be RAM-gated at runtime
    "claude",
]

# ── Quality scorer ────────────────────────────────────────────────────────────

def score_output(text: str, fixture: dict) -> tuple[int, str]:
    """
    Automated quality rubric 1-5. Returns (score, reason).
    Raw text is always preserved in the report for human eyeballing.

    1 = wrong language / irrelevant / empty
    2 = English only, no personality
    3 = correct language, robotic/formal tone
    4 = natural, some Tanglish flavor, feels like Cosmo
    5 = perfect Tanglish, personality clear, within max_words
    """
    if not text or len(text.strip()) < 2:
        return 1, "empty or too short"

    text_lower = text.lower().strip()

    # Tamil/Tanglish markers (common words transliterated)
    tanglish_words = {
        "da", "di", "dei", "bro", "machan", "pa", "ma", "yaar",
        "naan", "nee", "avan", "aval", "enna", "epdi", "eppo",
        "illa", "irukku", "variya", "ponga", "vanga", "sollu",
        "paaru", "theriyum", "konjam", "romba", "super", "oru",
        "indha", "adhu", "ithu", "unakku", "enakku", "aama",
    }
    has_tanglish = any(w in text_lower.split() for w in tanglish_words)

    # Robotic/formal markers
    robotic_markers = [
        "certainly", "absolutely", "i understand", "of course",
        "i am here to", "how can i assist", "as an ai",
        "i apologize", "please note", "it is important",
    ]
    is_robotic = any(m in text_lower for m in robotic_markers)

    word_count = len(text.split())
    max_words  = fixture.get("max_words", 15)
    within_limit = word_count <= max_words

    # Personality markers
    personality_words = [
        "!", "?", "haha", "lol", "oh", "hey", "wow", "yay",
        "sigh", "hmm", "aww", "ooh", "ugh",
    ]
    has_personality = any(w in text_lower for w in personality_words)

    if is_robotic:
        return 2, f"robotic tone detected; {'within' if within_limit else 'exceeds'} word limit"

    if has_tanglish and has_personality and within_limit:
        return 5, f"Tanglish + personality + {word_count}/{max_words} words"

    if has_tanglish and within_limit:
        return 4, f"Tanglish present + {word_count}/{max_words} words"

    if has_personality and within_limit:
        return 4, f"personality present + {word_count}/{max_words} words"

    if within_limit:
        return 3, f"correct language, {word_count}/{max_words} words, no strong personality"

    return 2, f"exceeds word limit ({word_count}/{max_words}) or flat tone"


# ── Model runners ─────────────────────────────────────────────────────────────

def run_ollama(model_tag: str, system: str, user: str, timeout: int = 30) -> dict:
    """Call Ollama API. Returns {text, latency_ms, tokens_in, tokens_out, error}."""
    import urllib.request
    import urllib.error

    model = model_tag.replace("ollama/", "")
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "stream": False,
        "options": {"num_predict": 80, "temperature": 0.7},
    }).encode()

    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
        latency_ms = int((time.perf_counter() - t0) * 1000)
        text = body.get("message", {}).get("content", "").strip()
        usage = body.get("usage", {})
        return {
            "text":        text,
            "latency_ms":  latency_ms,
            "tokens_in":   usage.get("prompt_tokens", 0),
            "tokens_out":  usage.get("completion_tokens", 0),
            "error":       None,
        }
    except Exception as e:
        return {
            "text":       "",
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "tokens_in":  0, "tokens_out": 0,
            "error":      str(e)[:120],
        }


def run_claude(system: str, user: str, timeout: int = 20) -> dict:
    """Call Claude Haiku. Returns same shape as run_ollama."""
    if not ANTHROPIC_KEY:
        return {"text": "", "latency_ms": 0, "tokens_in": 0, "tokens_out": 0,
                "error": "ANTHROPIC_API_KEY not set"}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        t0 = time.perf_counter()
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=80,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        text = resp.content[0].text.strip() if resp.content else ""
        return {
            "text":       text,
            "latency_ms": latency_ms,
            "tokens_in":  resp.usage.input_tokens,
            "tokens_out": resp.usage.output_tokens,
            "error":      None,
        }
    except Exception as e:
        return {"text": "", "latency_ms": 0, "tokens_in": 0, "tokens_out": 0,
                "error": str(e)[:120]}


def check_ollama_health(model_tag: str) -> tuple[bool, str]:
    """Returns (available, reason)."""
    import urllib.request
    model = model_tag.replace("ollama/", "")
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read())
        models = [m["name"].split(":")[0] for m in body.get("models", [])]
        base = model.split(":")[0]
        if base in models:
            return True, "available"
        return False, f"model '{model}' not pulled (ollama pull {model})"
    except Exception as e:
        return False, f"Ollama unreachable: {e}"


def free_ram_mb() -> int:
    try:
        data = Path("/proc/meminfo").read_text()
        for line in data.splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 9999  # unknown → don't gate


# ── Main runner ───────────────────────────────────────────────────────────────

def run_bakeoff(
    suite_dir: Path,
    models: list[str],
    output_path: Path,
    dry_run: bool = False,
) -> dict:
    fixtures = sorted(suite_dir.glob("*.json"))
    if not fixtures:
        print(f"No fixture files found in {suite_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(fixtures)} fixtures from {suite_dir}")

    # RAM gate for 3b
    ram_mb = free_ram_mb()
    skipped_models = {}
    active_models = []
    for m in models:
        if "3b" in m:
            if ram_mb < RAM_NEEDED_3B_MB:
                skipped_models[m] = f"SKIPPED_LOW_RAM (free={ram_mb}MB, need={RAM_NEEDED_3B_MB}MB)"
                print(f"  ⚠  {m}: {skipped_models[m]}")
                continue
            else:
                print(f"  ✓  {m}: RAM OK ({ram_mb}MB free)")

        if m.startswith("ollama/"):
            ok, reason = check_ollama_health(m)
            if not ok:
                skipped_models[m] = f"SKIPPED: {reason}"
                print(f"  ✗  {m}: {reason}")
                continue
            print(f"  ✓  {m}: {reason}")
        elif m == "claude":
            if not ANTHROPIC_KEY:
                skipped_models[m] = "SKIPPED: ANTHROPIC_API_KEY not set"
                print(f"  ✗  {m}: no API key")
                continue
            print(f"  ✓  {m}: API key present")

        active_models.append(m)

    if not active_models:
        print("No active models — nothing to run. Check Ollama and API key.", file=sys.stderr)
        sys.exit(1)

    if dry_run:
        print(f"\nDry run complete. {len(fixtures)} fixtures, {len(active_models)} models.")
        print(f"Active: {active_models}")
        if skipped_models:
            print(f"Skipped: {list(skipped_models.keys())}")
        return {}

    # ── Run ──────────────────────────────────────────────────────────────────
    results: dict[str, list[dict]] = {m: [] for m in active_models}
    call_counts = {m: 0 for m in active_models}

    for fx_path in fixtures:
        fixture = json.loads(fx_path.read_text())
        fid     = fixture.get("id", fx_path.stem)
        system  = fixture.get("system", "You are Cosmo, a small playful robot companion.")
        user    = fixture.get("user", "")
        print(f"\n  [{fid}]")

        for model in active_models:
            if call_counts[model] >= MAX_CALLS_PER_MODEL:
                print(f"    {model}: SKIPPED (call cap {MAX_CALLS_PER_MODEL} reached)")
                continue

            if model.startswith("ollama/"):
                result = run_ollama(model, system, user)
            elif model == "claude":
                result = run_claude(system, user)
            else:
                result = {"text": "", "latency_ms": 0, "tokens_in": 0, "tokens_out": 0,
                          "error": f"unknown model type: {model}"}

            call_counts[model] += 1

            if result["error"]:
                score, reason = 1, f"error: {result['error']}"
                print(f"    {model}: ERROR {result['error'][:60]}")
            else:
                score, reason = score_output(result["text"], fixture)
                preview = result["text"][:60].replace("\n", " ")
                print(f"    {model}: {result['latency_ms']}ms  score={score}  \"{preview}\"")

            results[model].append({
                "fixture_id":  fid,
                "scenario":    fixture.get("scenario", ""),
                "text":        result["text"],
                "latency_ms":  result["latency_ms"],
                "tokens_in":   result["tokens_in"],
                "tokens_out":  result["tokens_out"],
                "score":       score,
                "score_reason":reason,
                "error":       result["error"],
            })

    # ── Report ────────────────────────────────────────────────────────────────
    report = _build_report(results, skipped_models, fixtures, active_models, ram_mb)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)
    print(f"\nReport written to {output_path}")
    return results


def _build_report(results, skipped, fixtures, active_models, ram_mb) -> str:
    import datetime
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "# Cosmo LLM Bake-off Report",
        f"_Generated: {now} | RAM available: {ram_mb}MB_",
        "",
        "## Summary Table",
        "",
    ]

    # Header
    header = "| Fixture | Scenario |"
    sep    = "|---------|----------|"
    for m in active_models:
        short = m.replace("ollama/llama3.2:", "").replace("claude", "Claude")
        header += f" {short} score | {short} ms |"
        sep    += "------------|------|"
    lines += [header, sep]

    for fx_path in fixtures:
        fid      = json.loads(fx_path.read_text()).get("id", fx_path.stem)
        scenario = json.loads(fx_path.read_text()).get("scenario", "")[:40]
        row = f"| `{fid}` | {scenario} |"
        for m in active_models:
            model_results = {r["fixture_id"]: r for r in results.get(m, [])}
            r = model_results.get(fid)
            if r:
                row += f" {r['score']}/5 | {r['latency_ms']}ms |"
            else:
                row += " — | — |"
        lines.append(row)

    # Averages
    avg_row = "| **AVERAGE** | |"
    for m in active_models:
        rs = [r for r in results.get(m, []) if not r["error"]]
        if rs:
            avg_score = sum(r["score"]   for r in rs) / len(rs)
            avg_ms    = sum(r["latency_ms"] for r in rs) // len(rs)
            avg_row += f" **{avg_score:.1f}** | **{avg_ms}ms** |"
        else:
            avg_row += " — | — |"
    lines.append(avg_row)

    # Skipped models
    if skipped:
        lines += ["", "## Skipped Models", ""]
        for m, reason in skipped.items():
            lines.append(f"- **{m}**: {reason}")

    # Per-model totals
    lines += ["", "## Call Totals", ""]
    for m in active_models:
        rs = results.get(m, [])
        total_in  = sum(r["tokens_in"]  for r in rs)
        total_out = sum(r["tokens_out"] for r in rs)
        errors    = sum(1 for r in rs if r["error"])
        lines.append(f"- **{m}**: {len(rs)} calls, {total_in} tokens in, {total_out} out, {errors} errors")

    # Raw outputs
    lines += ["", "---", "", "## Raw Outputs (for human review)", ""]
    for fx_path in fixtures:
        fixture = json.loads(fx_path.read_text())
        fid      = fixture.get("id", fx_path.stem)
        scenario = fixture.get("scenario", "")
        lines += [f"### `{fid}` — {scenario}", "", f"**Prompt:** {fixture.get('user', '')}", ""]
        for m in active_models:
            model_results = {r["fixture_id"]: r for r in results.get(m, [])}
            r = model_results.get(fid)
            short = m.replace("ollama/llama3.2:", "llama3.2:").replace("claude", "Claude Haiku")
            if r:
                score_str = f"score={r['score']}/5 ({r['score_reason']})"
                text = r["text"] or f"_(error: {r['error']})_"
                lines += [f"**{short}** [{score_str}, {r['latency_ms']}ms]", "", f"> {text}", ""]
            else:
                lines += [f"**{short}** _(not run)_", ""]

    return "\n".join(lines) + "\n"


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Cosmo LLM bake-off harness")
    parser.add_argument("--suite",   default="tests/bakeoff/prompts/",
                        help="directory of fixture JSON files")
    parser.add_argument("--output",  default="docs/LLM_BAKEOFF_REPORT.md",
                        help="output markdown report path")
    parser.add_argument("--models",  nargs="+", default=DEFAULT_MODELS,
                        help="models to test: ollama/llama3.2:1b ollama/llama3.2:3b claude")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate fixtures and check model availability; no API calls")
    parser.add_argument("--list-fixtures", action="store_true",
                        help="list available fixtures and exit")
    args = parser.parse_args()

    suite_dir   = Path(args.suite)
    output_path = Path(args.output)

    if not suite_dir.exists():
        print(f"Suite directory not found: {suite_dir}", file=sys.stderr)
        sys.exit(1)

    if args.list_fixtures:
        fixtures = sorted(suite_dir.glob("*.json"))
        for f in fixtures:
            d = json.loads(f.read_text())
            print(f"  {f.stem:30s} {d.get('scenario','')[:60]}")
        return

    print(f"\nCosmo LLM Bake-off")
    print(f"Suite:  {suite_dir}  ({len(list(suite_dir.glob('*.json')))} fixtures)")
    print(f"Models: {args.models}")
    print(f"Output: {output_path}")
    print(f"RAM:    {free_ram_mb()}MB free\n")

    run_bakeoff(suite_dir, args.models, output_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
