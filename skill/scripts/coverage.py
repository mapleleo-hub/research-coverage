"""Bounded candidate discovery, mechanical screening, and upstream inspection. Python 3.10+, gh for native queries."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unicodedata
from urllib.error import URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

DEFAULTS = {"max_queries": 12, "max_inspections": 6, "limit": 10, "pages": 3, "screen": 25,
            "adjacent": 0, "max_upstream": 40}
BOUNDS = {"max_queries": (1, 40), "max_inspections": (2, 20), "limit": (1, 50), "pages": (1, 10),
          "screen": (1, 200), "adjacent": (0, 50), "max_upstream": (1, 200)}
PLAN_FIELDS = {"topic", "axes"} | set(BOUNDS)
RULE = "screen-v1"
UPSTREAM_RULE = "upstream-v1"
SEARCH_CAP = 1000
STOPWORDS = frozenset("""a an and are as at be by for from has have in into is it its of on or that the
their this to use used using via was were will with without your you our we all any can may not only
project projects repo repository tool tools library libraries app apps framework frameworks open source
agent agents ai application applications based new self""".split())
MANIFESTS = ("package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml", "pom.xml")


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def text(value, name, maximum=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name}: nonempty string required (max {maximum})")
    return value.strip()


def validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) - PLAN_FIELDS:
        raise ValueError("Invalid plan object or unknown fields")
    result = dict(plan, topic=text(plan.get("topic"), "topic"))
    for key, (low, high) in BOUNDS.items():
        value = plan.get(key, DEFAULTS[key])
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key}: integer in {low}..{high} required")
        result[key] = value
    if result["limit"] * result["pages"] > SEARCH_CAP:
        raise ValueError(f"limit*pages must stay within GitHub's {SEARCH_CAP}-result window")
    axes = plan.get("axes")
    if not isinstance(axes, list) or not axes:
        raise ValueError("axes: nonempty array required")
    seen = set()
    for axis in axes:
        if not isinstance(axis, dict) or set(axis) != {"id", "label", "queries"}:
            raise ValueError("Each axis requires id, label, queries only")
        ident = text(axis["id"], "axis.id", 40)
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", ident) or ident in seen:
            raise ValueError("Axis IDs must be unique lowercase identifiers")
        seen.add(ident)
        text(axis["label"], "axis.label", 120)
        queries = axis["queries"]
        if not isinstance(queries, list) or len(queries) < 2:
            raise ValueError("Each axis needs at least two complete queries")
        for query in queries:
            text(query, "query", 300)
        if len({normalized(q) for q in queries}) != len(queries):
            raise ValueError("Duplicate normalized queries within axis")
    if sum(len(a["queries"]) for a in axes) > result["max_queries"]:
        raise ValueError("Initial queries exceed max_queries; nothing was silently truncated")
    return result


def atomic_bytes(path, raw):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".coverage-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def unlock_run(run):
    """Explicit recovery only: removing a lock held by a live writer corrupts state."""
    lock = run / ".lock"
    if not lock.exists():
        raise ValueError("No lock file present")
    recorded = {}
    try:
        recorded = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        recorded = {"unreadable": True}
    lock.unlink()
    print(json.dumps({"removed": str(lock), "recorded": recorded,
                      "warning": "Confirm the recorded process ended before relying on this"}, ensure_ascii=False))


def setting(state, key):
    return state["plan"].get(key, DEFAULTS[key])


def initialize(plan_path, out):
    plan = validate_plan(read_json(plan_path))
    out.mkdir(parents=True, exist_ok=False)
    (out / "snapshots").mkdir()
    atomic_json(out / "plan.json", plan)
    now = stamp()
    state = {"version": 1, "created_at": now, "scoring_at": now, "plan": plan,
             "plan_sha256": digest((out / "plan.json").read_bytes()),
             "queries": [], "inspections": [], "upstreams": [], "screens": {}, "candidates": {}}
    atomic_json(out / "state.json", state)


@contextmanager
def locked(run):
    # A hard-killed writer leaves the lock; never guess that a running writer is dead.
    lock = run / ".lock"
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError:
        raise ValueError("Run is locked; verify the previous process ended before removing .lock") from None
    try:
        with handle:
            handle.write(json.dumps({"pid": os.getpid(), "started_at": stamp()}))
        yield
    finally:
        lock.unlink()


def load(run):
    state = read_json(run / "state.json")
    if state.get("version") != 1:
        raise ValueError("Unsupported state version")
    validate_plan(state["plan"])
    if digest((run / "plan.json").read_bytes()) != state["plan_sha256"] or read_json(run / "plan.json") != state["plan"]:
        raise ValueError("plan.json changed; start a new run instead of replacing its plan")
    # Runs created before v2 lack these keys; adding them in memory keeps old evidence readable.
    for key, default in (("queries", []), ("inspections", []), ("upstreams", []), ("screens", {}), ("candidates", {})):
        state.setdefault(key, default)
    return state


def save(run, state):
    atomic_json(run / "state.json", state)


def inside(run, relative):
    path = Path(relative)
    if path.is_absolute():
        raise ValueError("Absolute snapshot path rejected")
    target = (run / path).resolve()
    if not target.is_relative_to(run.resolve()):
        raise ValueError("Snapshot path escapes run directory")
    return target


def snapshot(run, record, raw):
    relative = "snapshots/" + record["id"] + ".json"
    path = inside(run, relative)
    atomic_bytes(path, raw)
    record.update(snapshot=relative, sha256=digest(raw))


RESERVED = {"orgs", "topics", "settings", "features", "sponsors", "marketplace", "login", "search", "collections", "users", "apps"}


def canonical(url):
    parts = urlsplit(text(url, "url", 4000))
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Public HTTP(S) URL without embedded credentials required")
    host = parts.hostname.lower()
    if host in ("github.com", "www.github.com"):
        segments = parts.path.strip("/").split("/")
        if len(segments) < 2 or segments[0].lower() in RESERVED:
            raise ValueError("GitHub repository URL required")
        repo = "/".join(segments[:2])
        if repo.lower().endswith(".git"):
            repo = repo[:-4]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("Invalid GitHub repository")
        return repo.lower(), "https://github.com/" + repo.lower()
    clean = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))
    return clean, clean


META_KEYS = ("stars", "pushed_at", "archived", "fork", "language", "topics", "license")


def add_candidate(state, item, origin, axes):
    ident, url = canonical(item["url"])
    row = state["candidates"].setdefault(ident, {"id": ident, "url": url,
        "title": str(item.get("title") or ident), "description": str(item.get("description") or ""),
        "origins": [], "axes": [], "decision": None, "reason": ""})
    for key in META_KEYS:
        if item.get(key) is not None and row.get(key) is None:
            row[key] = item[key]
    if origin not in row["origins"]:
        row["origins"].append(origin)
    row["axes"] = sorted(set(row["axes"]) | set(axes))
    return ident


def gh(argv):
    proc = subprocess.run(argv, capture_output=True, encoding="utf-8", timeout=40)
    if proc.returncode:
        raise ValueError(f"gh exit {proc.returncode}: {proc.stderr.strip()[:1000]}")
    raw = proc.stdout.encode("utf-8")
    return json.loads(proc.stdout), raw


def search_item(result):
    license_info = result.get("license") or {}
    return {"url": result["html_url"], "title": result["full_name"], "description": result.get("description"),
            "stars": result.get("stargazers_count"), "pushed_at": result.get("pushed_at"),
            "archived": bool(result.get("archived")), "fork": bool(result.get("fork")),
            "language": result.get("language"), "topics": (result.get("topics") or [])[:10],
            "license": license_info.get("spdx_id")}


def new_query(run, state, axis, query, kind, provider):
    if axis not in {a["id"] for a in state["plan"]["axes"]}:
        raise ValueError("Unknown axis")
    text(query, "query", 300)
    if len(state["queries"]) >= state["plan"]["max_queries"]:
        raise ValueError("Query budget exhausted")
    row = {"id": f"q{len(state['queries']) + 1:04}", "axis": axis, "query": query,
           "normalized": normalized(query), "kind": kind, "provider": provider,
           "status": "running", "started_at": stamp(), "candidate_ids": []}
    state["queries"].append(row)
    save(run, state)
    return row


def search(run, state, axis, query):
    row = new_query(run, state, axis, query, "native", "github-api")
    limit = setting(state, "limit")
    argv = ["gh", "api", "--method", "GET", "search/repositories", "-f",
            "q=" + query + " is:public archived:false", "-f", "per_page=" + str(limit)]
    row["argv"] = argv
    save(run, state)
    snapshots, ids, fetched, total, incomplete = [], [], 0, None, False
    try:
        for page in range(1, setting(state, "pages") + 1):
            data, raw = gh(argv + ["-f", "page=" + str(page)])
            relative = f"snapshots/{row['id']}.p{page}.json"
            atomic_bytes(inside(run, relative), raw)
            snapshots.append({"page": page, "file": relative, "sha256": digest(raw)})
            if not isinstance(data, dict) or not isinstance(data.get("items"), list) or type(data.get("total_count")) is not int:
                raise ValueError("GitHub returned an invalid search response")
            total = data["total_count"]
            incomplete = incomplete or bool(data.get("incomplete_results"))
            items = data["items"]
            prepared = [search_item(r) for r in items]
            for item in prepared:
                canonical(item["url"])
            ids += [add_candidate(state, item, row["id"], [axis]) for item in prepared]
            fetched += len(items)
            if len(items) < limit or fetched >= total:
                break
        distinct = len(set(ids))
        row.update(snapshot=snapshots[0]["file"], sha256=snapshots[0]["sha256"], snapshots=snapshots,
                   candidate_ids=list(dict.fromkeys(ids)), count=distinct, items_seen=fetched,
                   total_count=total, pages_fetched=len(snapshots), truncated=total > fetched,
                   coverage=round(fetched / total, 6) if total else None,
                   status="partial" if incomplete else "ok", incomplete_results=incomplete)
    except Exception as exc:
        row["snapshots"] = snapshots
        row.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        raise ValueError(row["error"]) from None
    finally:
        row["finished_at"] = stamp()
        save(run, state)


def import_results(run, state, axis, query, file):
    raw = file.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Imported result must be an object")
    provider = text(data.get("provider"), "provider", 120)
    items = data.get("results")
    if not isinstance(items, list) or len(items) > 500:
        raise ValueError("results: array of at most 500 objects required")
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Result must be an object")
        canonical(item.get("url"))
    row = new_query(run, state, axis, query, "imported", provider)
    try:
        snapshot(run, row, raw)
        row["candidate_ids"] = [add_candidate(state, item, row["id"], [axis]) for item in items]
        row.update(status="ok", count=len(set(row["candidate_ids"])), total_count=None, truncated=None,
                   provenance="Provided file; provider execution not independently verified")
    except Exception as exc:
        row.update(status="failed", error=str(exc))
        raise
    finally:
        row["finished_at"] = stamp()
        save(run, state)


def inspect_repo(run, state, repo):
    ident, _ = canonical("https://github.com/" + repo)
    if ident not in state["candidates"]:
        raise ValueError("inspect only accepts previously discovered repositories")
    if len(state["inspections"]) >= state["plan"]["max_inspections"]:
        raise ValueError("Inspection budget exhausted")
    row = {"id": f"i{len(state['inspections']) + 1:04}", "repo": ident,
           "status": "running", "started_at": stamp(), "candidate_ids": []}
    state["inspections"].append(row)
    argv = ["gh", "api", f"repos/{ident}/readme"]
    row["argv"] = argv
    save(run, state)
    try:
        data, raw = gh(argv)
        snapshot(run, row, raw)
        if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
            raise ValueError("README was not returned as base64")
        content = base64.b64decode(data["content"]).decode("utf-8")
        relative = f"snapshots/{row['id']}.readme.md"
        atomic_bytes(inside(run, relative), content.encode("utf-8"))
        row.update(readme_snapshot=relative, readme_sha256=digest(content.encode("utf-8")))
        for match in re.findall(r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", content):
            try:
                linked, _ = canonical(match.rstrip("."))
            except ValueError:
                continue
            if linked != ident:
                row["candidate_ids"].append(add_candidate(state, {"url": match.rstrip(".")}, row["id"], state["candidates"][ident]["axes"]))
        row.update(status="ok", candidate_ids=sorted(set(row["candidate_ids"])),
                   source_url=data.get("html_url"), note="README links are unverified leads, not recommendations")
    except Exception as exc:
        row.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        raise ValueError(row["error"]) from None
    finally:
        row["finished_at"] = stamp()
        save(run, state)


def review_candidate(run, state, ident, decision, reason):
    key = ident if ident in state["candidates"] else canonical(ident if "://" in ident else "https://github.com/" + ident)[0]
    if key not in state["candidates"] or decision not in ("shortlist", "exclude", "defer"):
        raise ValueError("Known candidate and valid decision required")
    reason = text(reason, "reason", 2000)
    row = state["candidates"][key]
    row.setdefault("review_history", []).append({"at": stamp(), "decision": decision, "reason": reason})
    row.update(decision=decision, reason=reason)
    save(run, state)


def tokens_for(axis):
    words = set()
    for query in axis["queries"]:
        for token in re.findall(r"[a-z0-9][a-z0-9+.#_-]{2,}", normalized(query)):
            if token not in STOPWORDS:
                words.add(token)
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", query):
            words.add(chunk)
            words.update(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return words


def days_since(value, reference=None):
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ((reference or datetime.now(timezone.utc)) - moment).days


def scoring_reference(state):
    """A fixed clock keeps rescreening reproducible; old runs fall back to created_at."""
    raw = state.get("scoring_at") or state.get("created_at")
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def hard_filter(row):
    if row.get("archived"):
        return "archived"
    if row.get("fork"):
        return "fork"
    if not (row.get("description") or "").strip() and not row.get("topics"):
        return "no_description"
    return None


def score_candidate(row, words, reference=None):
    haystack = normalized(" ".join([row.get("title") or "", row.get("description") or "",
                                    " ".join(row.get("topics") or [])]))
    hits = sorted(word for word in words if word and word in haystack)
    relevance = 40.0 * min(len(hits) / max(1, min(len(words), 10)), 1.0)
    origins = min(len(row.get("origins") or []), 3) * 6.0
    age = days_since(row.get("pushed_at"), reference)
    freshness = 0.0 if age is None else (15.0 if age <= 90 else 8.0 if age <= 365 else 3.0 if age <= 730 else 0.0)
    stars = min(12.0, math.log10((row.get("stars") or 0) + 1) * 3.0)
    licensed = 5.0 if row.get("license") and row.get("license") != "NOASSERTION" else 0.0
    topical = 5.0 if row.get("topics") and hits else 0.0
    reasons = [f"命中词{len(hits)}", f"来源{len(row.get('origins') or [])}"]
    if freshness >= 15:
        reasons.append("近90天有提交")
    elif age is not None and age > 730:
        reasons.append("停更超两年")
    reasons.append(str(row["license"]) if row.get("license") else "无许可证")
    return min(100, round(relevance + origins + freshness + stars + licensed + topical)), reasons


def card(row, score, reasons):
    return {"id": row["id"], "url": row["url"], "title": row["title"],
            "description": (row.get("description") or "")[:200], "score": score, "reasons": reasons,
            "stars": row.get("stars"), "pushed_at": row.get("pushed_at"), "language": row.get("language"),
            "license": row.get("license"), "origins": list(row.get("origins") or []),
            "decision": row.get("decision")}


def description_tokens(row):
    """Forks, mirrors and split repos share one description even across owners."""
    return {token for token in re.findall(r"[a-z0-9]{3,}", normalized(row.get("description") or ""))
            if token not in STOPWORDS}


def similar_description(left, right):
    if not left or not right:
        return False
    return len(left & right) / len(left | right) >= 0.8


def screen_axis(run, state, axis):
    rows = [row for row in state["candidates"].values() if axis["id"] in row["axes"]]
    words = tokens_for(axis)
    reference = scoring_reference(state)
    frozen = state.get("scoring_at") or state.get("created_at") or stamp()
    filtered, scored = [], []
    for row in rows:
        why = hard_filter(row)
        if why:
            filtered.append({"id": row["id"], "rule": why})
            continue
        score, reasons = score_candidate(row, words, reference)
        scored.append((score, row, reasons))
    scored.sort(key=lambda item: (-item[0], -(item[1].get("stars") or 0), item[1]["id"]))
    limit = setting(state, "screen")
    picked, duplicates, seen = [], [], []
    index = 0
    while index < len(scored) and len(picked) < limit:
        item = scored[index]
        index += 1
        tokens = description_tokens(item[1])
        if any(similar_description(tokens, other) for other in seen):
            duplicates.append(dict(card(item[1], item[0], item[2]), filtered_by="duplicate_description"))
            continue
        seen.append(tokens)
        picked.append(item)
    chosen = [card(row, score, reasons) for score, row, reasons in picked]
    rest_all = [dict(card(row, score, reasons), filtered_by="below_cut") for score, row, reasons in scored[index:]]
    near = rest_all[:setting(state, "adjacent")]
    rest = rest_all[len(near):] + duplicates
    rest += [{"id": item["id"], "filtered_by": item["rule"]} for item in filtered]
    record = {"axis": axis["id"], "label": axis["label"], "rule": RULE, "k": limit, "total": len(rows),
              "screened": len(chosen), "deferred": len(rest), "filtered": len(filtered),
              "duplicated": len(duplicates), "adjacent": len(near), "cards": chosen, "near": near}
    atomic_json(run / "cards" / (axis["id"] + ".json"), {"axis": axis["id"], "label": axis["label"], "rule": RULE,
                "k": limit, "generated_at": frozen, "total": len(rows), "screened": len(chosen),
                "deferred": len(rest), "filtered": len(filtered), "duplicated": len(duplicates),
                "adjacent": len(near), "items": chosen,
                "note": "Ranking is keyword and metadata arithmetic, not a relevance verdict"})
    atomic_json(run / "cards" / (axis["id"] + ".adjacent.json"), {"axis": axis["id"], "rule": RULE,
                "generated_at": frozen, "items": near,
                "note": "Just below the cut, listed for awareness only; no review reason required"})
    atomic_json(run / "cards" / (axis["id"] + ".deferred.json"), {"axis": axis["id"], "rule": RULE,
                "generated_at": frozen, "items": rest})
    atomic_bytes(run / "cards" / (axis["id"] + ".md"), render_cards(axis, record).encode("utf-8"))
    return record


def render_cards(axis, record):
    lines = [f"# {axis['label']} 送审卡片", "",
             f"规则 {record['rule']}；候选 {record['total']}，送审 {record['screened']}，"
             f"未送审 {record['deferred']}，硬过滤 {record['filtered']}。",
             "分数来自关键词与仓库元数据，只用于排序，不构成相关性结论。", "",
             "| 候选 | 分 | ★ | 语言 | 最近提交 | 许可证 | 描述（未核实） | 命中来源 |", "|---|---:|---:|---|---|---|---|---|"]
    for item in record["cards"]:
        stars = item["stars"] if isinstance(item["stars"], int) else "-"
        lines.append("| " + " | ".join([cell(item["id"]), str(item["score"]), str(stars), cell(item["language"] or "-"),
                                        (item["pushed_at"] or "-")[:10], cell(item["license"] or "-"),
                                        cell(item["description"]), cell(", ".join(item["origins"]))]) + " |")
    if record["near"]:
        lines += ["", "## 紧邻候补（未送审，只是让你知道存在）", ""]
        for item in record["near"]:
            lines.append(f"- {item['id']}（{item['score']}）{item['description'][:80]}")
    return "\n".join(lines) + "\n"


def screen_run(run, state):
    (run / "cards").mkdir(exist_ok=True)
    records = [screen_axis(run, state, axis) for axis in state["plan"]["axes"]]
    state["screens"] = {record["axis"]: {key: value for key, value in record.items() if key not in ("cards", "near")}
                        for record in records}
    save(run, state)
    return records


def http_json(url, timeout=20, total=30):
    """Socket timeout bounds each read; a daemon thread bounds the total wait without blocking exit."""
    box = {}

    def worker():
        try:
            box["data"] = _fetch_json(url, timeout)
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(total)
    if thread.is_alive():
        raise ValueError(f"deps.dev request exceeded {total}s total budget")
    if "error" in box:
        exc = box["error"]
        raise ValueError(f"deps.dev request failed: {type(exc).__name__}: {exc}") from None
    return box["data"]


def _fetch_json(url, timeout):
    request = Request(url, headers={"User-Agent": "research-coverage/2 (+local cli)"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def deps_package(system, name):
    encoded = quote(name, safe="")
    package = http_json(f"https://api.deps.dev/v3/systems/{system}/packages/{encoded}")
    versions = [v for v in package.get("versions") or [] if isinstance(v, dict)]
    chosen = next((v for v in versions if v.get("isDefault")), versions[-1] if versions else None)
    row = {"system": system, "name": name, "versions": len(versions)}
    if not chosen:
        row["error"] = "no published versions"
        return row
    version = (chosen.get("versionKey") or {}).get("version")
    row.update(version=version, deprecated=bool(chosen.get("isDeprecated")))
    detail = http_json(f"https://api.deps.dev/v3/systems/{system}/packages/{encoded}/versions/{quote(str(version), safe='')}")
    row["licenses"] = detail.get("licenses") or []
    row["advisories"] = [item.get("id") for item in detail.get("advisoryKeys") or [] if isinstance(item, dict)]
    status = detail.get("projectStatus") or {}
    if status.get("status"):
        row["projectStatus"] = status["status"]
    return row


def split_spec(value):
    return re.split(r"[<>=!~\[;,\s]", value.strip())[0].strip()


def parse_manifest(name, body):
    found = []
    if name == "package.json":
        data = json.loads(body)
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            section = data.get(key)
            if isinstance(section, dict):
                found += [("NPM", dep) for dep in section]
    elif name == "pyproject.toml":
        for block in re.findall(r"dependencies\s*=\s*\[(.*?)\]", body, re.S):
            found += [("PYPI", split_spec(item)) for item in re.findall(r"['\"]([^'\"]+)['\"]", block)]
        poetry = re.search(r"^\[tool\.poetry\.dependencies\](.*?)(?=^\[|\Z)", body, re.S | re.M)
        if poetry:
            found += [("PYPI", key) for key in re.findall(r"^([A-Za-z0-9_.\-]+)\s*=", poetry.group(1), re.M)]
    elif name == "requirements.txt":
        for line in body.splitlines():
            line = line.split("#")[0].strip()
            if line and not line.startswith("-"):
                found.append(("PYPI", split_spec(line)))
    elif name == "go.mod":
        for block in re.findall(r"require\s*\((.*?)\)", body, re.S):
            for line in block.splitlines():
                parts = line.split()
                if parts:
                    found.append(("GO", parts[0]))
        for single in re.findall(r"^require\s+(\S+)", body, re.M):
            found.append(("GO", single))
    elif name == "Cargo.toml":
        section = re.search(r"^\[dependencies\](.*?)(?=^\[|\Z)", body, re.S | re.M)
        if section:
            found += [("CARGO", key) for key in re.findall(r"^([A-Za-z0-9_\-]+)\s*=", section.group(1), re.M)]
    elif name == "pom.xml":
        # Shallow regex parse: Maven property interpolation and parent POMs are not resolved.
        found += [("MAVEN", item) for item in re.findall(r"<artifactId>([^<]+)</artifactId>", body)[1:]]
    return [(system, item) for system, item in found if item and len(item) < 200]


def gh_manifest(repo, name):
    try:
        data, raw = gh(["gh", "api", f"repos/{repo}/contents/{name}"])
    except ValueError:
        return None, None
    if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
        return None, None
    return base64.b64decode(data["content"]).decode("utf-8", "replace"), raw


def upstream_run(run, state, repo):
    ident, _ = canonical("https://github.com/" + repo)
    if ident not in state["candidates"]:
        raise ValueError("upstream only accepts previously discovered repositories")
    if len(state["upstreams"]) >= state["plan"]["max_inspections"]:
        raise ValueError("Upstream budget is shared with inspect (max_inspections); exhausted")
    budget = setting(state, "max_upstream")
    row = {"id": f"u{len(state['upstreams']) + 1:04}", "repo": ident, "status": "running",
           "started_at": stamp(), "manifests": []}
    state["upstreams"].append(row)
    save(run, state)
    try:
        declared, seen = [], set()
        for name in MANIFESTS:
            body, _ = gh_manifest(ident, name)
            if body is None:
                continue
            row["manifests"].append(name)
            for system, package in parse_manifest(name, body):
                key = (system, package.lower())
                if key not in seen:
                    seen.add(key)
                    declared.append((system, package))
        results = []
        for system, package in declared[:budget]:
            try:
                results.append(deps_package(system, package))
            except Exception as exc:
                results.append({"system": system, "name": package, "error": type(exc).__name__ + ": " + str(exc)})
        (run / "upstream").mkdir(exist_ok=True)
        relative = f"upstream/{row['id']}.{ident.replace('/', '__')}.json"
        atomic_json(inside(run, relative), {"repo": ident, "rule": UPSTREAM_RULE, "generated_at": stamp(),
                    "manifests": row["manifests"], "declared": len(declared), "truncated": len(declared) > budget,
                    "packages": results,
                    "note": "deps.dev default-version resolution is not the project's locked version; the public API exposes no reverse-dependency counts"})
        row.update(status="ok", count=len(results), failures=sum(1 for item in results if item.get("error")),
                   advisories=sum(len(item.get("advisories") or []) for item in results),
                   declared=len(declared), truncated=len(declared) > budget, result_file=relative,
                   packages=[{"system": item["system"], "name": item["name"], "version": item.get("version"),
                              "licenses": item.get("licenses"), "advisories": len(item.get("advisories") or []),
                              "error": item.get("error")} for item in results],
                   note="Default-version resolution; not the project's locked versions")
    except Exception as exc:
        row.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        raise ValueError(row["error"]) from None
    finally:
        row["finished_at"] = stamp()
        save(run, state)


def unresolved(records, kind):
    latest = {}
    for row in records:
        key = (row.get("kind"), row.get("axis"), normalized(row.get("query", ""))) if kind == "query" else row["repo"]
        latest[key] = row
    return [r["id"] for r in latest.values() if r["status"] != "ok"]


def evidence_paths(row):
    paths = [(item["file"], item["sha256"]) for item in row.get("snapshots") or []]
    if "snapshot" in row and (row["snapshot"], row["sha256"]) not in paths:
        paths.append((row["snapshot"], row["sha256"]))
    return paths


def audit(run, state, canaries=None):
    screens = screen_run(run, state)
    issues = []
    records = state["queries"] + state["inspections"]
    for row in records:
        paths = evidence_paths(row)
        if not paths and row["status"] == "ok":
            issues.append("missing_snapshot:" + row["id"])
        for relative, expected in paths:
            try:
                if digest(inside(run, relative).read_bytes()) != expected:
                    issues.append("snapshot_changed:" + row["id"])
            except (OSError, ValueError):
                issues.append("snapshot_unreadable_or_escaped:" + row["id"])
        if "readme_snapshot" in row:
            try:
                if digest(inside(run, row["readme_snapshot"]).read_bytes()) != row["readme_sha256"]:
                    issues.append("readme_changed:" + row["id"])
            except (OSError, ValueError):
                issues.append("readme_unreadable_or_escaped:" + row["id"])
    errors = unresolved(state["queries"], "query") + unresolved(state["inspections"], "inspection")
    issues += ["unresolved_attempt:" + ident for ident in errors]
    axes, coverage, card_items = [], [], []
    screen_by_axis = {record["axis"]: record for record in screens}
    for axis in state["plan"]["axes"]:
        queries = [r for r in state["queries"] if r["axis"] == axis["id"] and r["status"] == "ok"]
        # Imports consume budget and show up in reports, but cannot stand in for native coverage.
        unique = {normalized(r["query"]) for r in queries if r["kind"] == "native"}
        candidates = [r for r in state["candidates"].values() if axis["id"] in r["axes"]]
        if len(unique) < 2:
            issues.append("insufficient_distinct_queries:" + axis["id"])
        for planned in axis["queries"]:
            if normalized(planned) not in unique:
                issues.append("planned_query_not_run:" + axis["id"] + ":" + planned)
        if not candidates:
            issues.append("no_candidates_found:" + axis["id"])
        record = screen_by_axis[axis["id"]]
        fetched = sum(r.get("items_seen") or 0 for r in state["queries"] if r["axis"] == axis["id"])
        axes.append({"id": axis["id"], "label": axis["label"], "distinct_queries": len(unique),
                     "native_queries": sum(r["kind"] == "native" for r in queries),
                     "imported_queries": sum(r["kind"] == "imported" for r in queries),
                     "candidates": len(candidates), "fetched": fetched,
                     "screened": record["screened"], "deferred": record["deferred"], "filtered": record["filtered"],
                     "duplicated": record["duplicated"], "adjacent": record["adjacent"]})
        coverage.append({"axis": axis["id"], "label": axis["label"], "total": record["total"],
                         "screened": record["screened"], "deferred": record["deferred"],
                         "filtered": record["filtered"], "duplicated": record["duplicated"], "adjacent": record["adjacent"],
                         "cards": f"cards/{axis['id']}.md", "adjacent_file": f"cards/{axis['id']}.adjacent.json",
                         "deferred_file": f"cards/{axis['id']}.deferred.json"})
        card_items += [dict(item, axis=axis["id"]) for item in record["cards"]]
    inspected = {r["repo"] for r in state["inspections"] if r["status"] == "ok"}
    if len(inspected) < 2:
        issues.append("fewer_than_two_readme_origins")
    pending = [ident for ident in dict.fromkeys(item["id"] for item in card_items)
               if state["candidates"][ident]["decision"] not in ("shortlist", "exclude") or not state["candidates"][ident]["reason"].strip()]
    if pending:
        issues.append("screened_candidates_need_review:" + str(len(pending)))
    if len(state["queries"]) > state["plan"]["max_queries"] or len(state["inspections"]) > state["plan"]["max_inspections"]:
        issues.append("budget_exceeded")
    reference = None
    if canaries is not None:
        if not isinstance(canaries, list) or not canaries or not all(isinstance(c, str) and c.strip() for c in canaries):
            raise ValueError("Canaries must be a nonempty JSON string array")
        expected = {canonical(c if "://" in c else "https://github.com/" + c)[0] for c in canaries}
        if any("://" in ident for ident in expected):
            raise ValueError("Reference items must identify GitHub repositories")
        found = expected & state["candidates"].keys()
        contaminated = []
        for query in state["queries"]:
            folded = normalized(query["query"])
            if any(ident in folded or re.search(r"(?<![a-z0-9])" + re.escape(ident.rsplit("/", 1)[-1]) + r"(?![a-z0-9])", folded) for ident in expected):
                contaminated.append(query["id"])
        reference = {"found": sorted(found), "missing": sorted(expected - found), "found_count": len(found),
                     "reference_count": len(expected), "contaminated_queries": contaminated,
                     "scope": "Named-reference recovery only; not global recall or proof of blindness"}
        if expected - found:
            issues.append("reference_items_missing")
        if contaminated:
            issues.append("reference_query_contamination")
    imported = sum(r["kind"] == "imported" for r in state["queries"])
    status = "needs_attention" if issues else ("documented_with_imports" if imported else "process_checked")
    result = {"status": status, "topic": state["plan"]["topic"], "created_at": stamp(), "issues": issues,
              "state_sha256": digest((run / "state.json").read_bytes()),
              "axes": axes, "coverage": coverage, "card_items": card_items,
              "queries": state["queries"], "inspections": state["inspections"],
              "upstreams": [{key: value for key, value in row.items() if key != "packages"} for row in state["upstreams"]],
              "candidates": list(state["candidates"].values()), "pending_candidates": pending, "reference_check": reference,
              "unscreened_candidates": len(state["candidates"]) - len({item["id"] for item in card_items}),
              "historical_failed_attempts": sum(r["status"] != "ok" for r in records),
              "unresolved_attempts": errors, "imported_attempts": imported,
              "truncated_queries": [r["id"] for r in state["queries"] if r.get("truncated")],
              "limits": "Mechanical execution/screening checks only; query quality, relevance and ecosystem completeness remain unproven. Imports are supplied evidence, not native execution proof. Screening is keyword arithmetic, so long-tail candidates can rank below the cut."}
    atomic_json(run / "report.json", result)
    render_report(run / "report.md", result)
    return result


def cell(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_report(path, result):
    lines = ["# 候选覆盖报告", "", f"状态：{result['status']}", "", cell(result["limits"]), "",
             "## 检索方向", "", "| 方向 | 不同查询 | 原生 | 导入 | 抓取 | 候选 | 送审 | 未送审 | 硬过滤 | 同描述重复 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in result["axes"]:
        lines.append(f"| {cell(row['label'])} | {row['distinct_queries']} | {row['native_queries']} | {row['imported_queries']} | "
                     f"{row['fetched']} | {row['candidates']} | {row['screened']} | {row['deferred']} | {row['filtered']} | {row['duplicated']} |")
    lines += ["", "## 上下文口径", "",
              "模型只需读 cards/*.md 的送审卡片；未送审候选在 cards/*.deferred.json，不进入对话上下文。", ""]
    for row in result["coverage"]:
        lines.append(f"- {cell(row['label'])}：送审 {row['screened']} / 共 {row['total']}，"
                     f"未送审 {row['deferred']}（{row['deferred_file']}），邻域候补 {row['adjacent']}，卡片 {row['cards']}")
    lines += ["", "## 未闭环项", ""] + (["- " + cell(i) for i in result["issues"]] or ["机械项无缺口；不构成找全证明。"])
    lines += ["", "## 送审候选", "", "| 候选 | 分 | ★ | 语言 | 最近提交 | 许可证 | 描述（未核实） | 方向 | 来源 |", "|---|---:|---:|---|---|---|---|---|---|"]
    for row in result["card_items"]:
        stars = row["stars"] if isinstance(row["stars"], int) else "-"
        lines.append(f"| {cell(row['id'])} | {row['score']} | {stars} | {cell(row['language'] or '-')} | "
                     f"{(row['pushed_at'] or '-')[:10]} | {cell(row['license'] or '-')} | {cell(row['description'])} | "
                     f"{cell(row['axis'])} | {cell(', '.join(row['origins']))} |")
    if result["upstreams"]:
        lines += ["", "## 上游基底", "", "| 仓库 | 清单 | 声明依赖 | 已查 | 失败 | 漏洞条目 | 结果文件 |", "|---|---|---:|---:|---:|---:|---|"]
        for row in result["upstreams"]:
            lines.append(f"| {cell(row['repo'])} | {cell(', '.join(row.get('manifests') or []) or '-')} | "
                         f"{row.get('declared', '-')} | {row.get('count', '-')} | {row.get('failures', '-')} | "
                         f"{row.get('advisories', '-')} | {cell(row.get('result_file') or row.get('error') or '-')} |")
        lines += ["", "版本为 deps.dev 默认版本解析，不是项目锁定版本；公开 API 无反向依赖计数。"]
    lines += ["", "## 边界", "", "截断查询：" + cell(result["truncated_queries"]),
              "未送审候选：" + str(result["unscreened_candidates"]) + "（只需在需要时从 deferred 文件复查，不必逐条给理由）",
              "导入尝试：" + str(result["imported_attempts"]), "历史未成功尝试：" + str(result["historical_failed_attempts"]),
              "未解决尝试：" + cell(result["unresolved_attempts"])]
    if result["reference_check"]:
        lines += ["", "## 参考集合回归", "", cell(json.dumps(result["reference_check"], ensure_ascii=False))]
    atomic_bytes(path, ("\n".join(lines) + "\n").encode("utf-8"))


def run_initial(run, state):
    if unresolved(state["queries"], "query"):
        raise ValueError("Resolve failed/running attempts with explicit search before resuming run")
    for axis in state["plan"]["axes"]:
        for query in axis["queries"]:
            if not any(r["kind"] == "native" and r["axis"] == axis["id"] and normalized(r["query"]) == normalized(query) and r["status"] == "ok" for r in state["queries"]):
                search(run, state, axis["id"], query)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init"); init.add_argument("--plan", type=Path, required=True); init.add_argument("--out", type=Path, required=True)
    for name in ("run", "search", "import", "inspect", "review", "audit", "screen", "upstream", "unlock"):
        command = sub.add_parser(name); command.add_argument("--run", type=Path, required=True)
        if name in ("search", "import"):
            command.add_argument("--axis", required=True); command.add_argument("--query", required=True)
        if name == "import": command.add_argument("--file", type=Path, required=True)
        if name in ("inspect", "upstream"): command.add_argument("--repo", required=True)
        if name == "review":
            command.add_argument("--id", required=True); command.add_argument("--decision", choices=("shortlist", "exclude", "defer"), required=True); command.add_argument("--reason", required=True)
        if name == "audit": command.add_argument("--canaries", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            initialize(args.plan, args.out)
            print(str(args.out)); return 0
        run = args.run.resolve()
        if args.command == "unlock":
            unlock_run(run)
            return 0
        with locked(run):
            state = load(run)
            if args.command == "run": run_initial(run, state)
            elif args.command == "search": search(run, state, args.axis, args.query)
            elif args.command == "import": import_results(run, state, args.axis, args.query, args.file)
            elif args.command == "inspect": inspect_repo(run, state, args.repo)
            elif args.command == "upstream": upstream_run(run, state, args.repo)
            elif args.command == "review": review_candidate(run, state, args.id, args.decision, args.reason)
            elif args.command == "screen":
                for record in screen_run(run, state):
                    print(f"{record['axis']}: screened {record['screened']} of {record['total']} "
                          f"(deferred {record['deferred']}, filtered {record['filtered']})")
                return 0
            elif args.command == "audit":
                result = audit(run, state, read_json(args.canaries) if args.canaries else None)
                print(json.dumps({"status": result["status"], "issues": result["issues"],
                                  "coverage": [{k: row[k] for k in ("axis", "screened", "deferred", "filtered")} for row in result["coverage"]],
                                  "reference_check": result["reference_check"]}, ensure_ascii=False))
                return 2 if result["issues"] else 0
        print(args.command + " completed"); return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
