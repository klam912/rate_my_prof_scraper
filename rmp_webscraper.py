"""
RateMyProfessor Web Scraper - Direct GraphQL Edition
=====================================================
Calls RMP's GraphQL API directly using a browser session cookie for auth.
Each row in the output DataFrame = one student evaluation.

Columns
-------
    professor_name, school_name, school_id, school_location, department,
    professor_avg_rating, professor_avg_difficulty, professor_num_ratings,
    professor_would_take_again, evaluation_date, evaluation_class,
    evaluation_helpful, evaluation_clarity, evaluation_difficulty,
    evaluation_rating (= (helpful+clarity)/2), evaluation_comment,
    all_evaluations_json

Checkpoint / resume
-------------------
    Progress is saved to <output>.checkpoint.jsonl after every school.
    If the script is interrupted, re-run the exact same command and it will
    skip already-completed schools automatically.
    Once all schools are done the checkpoint file is deleted.
    Use --reset to discard the checkpoint and start fresh.

Usage
-----
    python rmp_scraper.py --schools 389 1381 --cookie-file cookie.txt
    python rmp_scraper.py --all-schools --cookie-file cookie.txt --workers 4
    python rmp_scraper.py --all-schools --cookie-file cookie.txt --reset

Cookie refresh
--------------
    When you get 403s:
    1. Visit ratemyprofessors.com in Chrome
    2. DevTools -> Network -> filter "graphql" -> click any request
    3. Headers -> Request Headers -> copy the "cookie" value
    4. Save to cookie.txt and re-run
"""

import argparse
import base64
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import pandas as pd
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GQL_URL = "https://www.ratemyprofessors.com/graphql"

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Origin": "https://www.ratemyprofessors.com",
    "Referer": "https://www.ratemyprofessors.com/",
}

PROF_PAGE_SIZE = 20
SLEEP_BETWEEN_REQUESTS = 0.3

COLUMN_ORDER = [
    "professor_name",
    "school_name",
    "school_id",
    "school_location",
    "department",
    "professor_avg_rating",
    "professor_avg_difficulty",
    "professor_num_ratings",
    "professor_would_take_again",
    "evaluation_date",
    "evaluation_class",
    "evaluation_helpful",
    "evaluation_clarity",
    "evaluation_difficulty",
    "evaluation_rating",
    "evaluation_comment",
    "all_evaluations_json",
]

# ---------------------------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------------------------

SCHOOL_QUERY = """
query SchoolQuery($id: ID!) {
  node(id: $id) {
    ... on School {
      id
      name
      city
      state
      country
    }
  }
}
"""

PROFESSORS_QUERY = """
query TeacherSearchPaginationQuery(
  $count: Int!
  $cursor: String
  $query: TeacherSearchQuery!
) {
  search: newSearch {
    teachers(query: $query, first: $count, after: $cursor) {
      edges {
        cursor
        node {
          id
          legacyId
          firstName
          lastName
          department
          avgRating
          avgDifficulty
          numRatings
          wouldTakeAgainPercent
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""

PROFESSOR_RATINGS_QUERY = """
query TeacherRatingsPageQuery($id: ID!) {
  node(id: $id) {
    ... on Teacher {
      id
      legacyId
      firstName
      lastName
      department
      avgRating
      avgDifficulty
      numRatings
      wouldTakeAgainPercent
      school {
        name
        city
        state
        country
      }
      ratings(first: 1000) {
        edges {
          node {
            date
            class
            helpfulRating
            clarityRating
            difficultyRating
            comment
          }
        }
      }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def checkpoint_path(output_prefix: str) -> Path:
    return Path(output_prefix).with_suffix(".checkpoint.jsonl")


def load_checkpoint(output_prefix: str) -> tuple[set[str], list[dict]]:
    """
    Reads the checkpoint file and returns (completed_school_ids, all_rows_so_far).
    Each line in the file is: {"school_id": "...", "rows": [...]}
    """
    path = checkpoint_path(output_prefix)
    completed: set[str] = set()
    all_rows: list[dict] = []

    if not path.exists():
        return completed, all_rows

    log.info("Found checkpoint file: %s", path)
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                sid = str(entry["school_id"])
                completed.add(sid)
                all_rows.extend(entry["rows"])
            except Exception as exc:
                log.warning("Skipping corrupt checkpoint line: %s", exc)

    log.info("  Resuming: %d schools already done, %d rows loaded.",
             len(completed), len(all_rows))
    return completed, all_rows


def save_checkpoint(output_prefix: str, school_id: str, rows: list[dict]) -> None:
    """Append one completed school to the checkpoint file."""
    path = checkpoint_path(output_prefix)
    entry = {"school_id": school_id, "rows": rows}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def clear_checkpoint(output_prefix: str) -> None:
    path = checkpoint_path(output_prefix)
    if path.exists():
        path.unlink()
        log.info("Checkpoint deleted: %s", path)


# ---------------------------------------------------------------------------
# GraphQL client
# ---------------------------------------------------------------------------

class RMPClient:
    def __init__(self, cookie: str):
        self.session = requests.Session()
        self.session.headers.update(BASE_HEADERS)
        self.session.headers["cookie"] = cookie

    def _post(self, query: str, variables: dict, retries: int = 3) -> dict:
        payload = {"query": query, "variables": variables}
        for attempt in range(retries):
            try:
                resp = self.session.post(GQL_URL, json=payload, timeout=30)
                if resp.status_code == 403:
                    raise RuntimeError(
                        "\n\n  403 Forbidden - your cookie has expired.\n"
                        "  Get a fresh one:\n"
                        "    1. Visit ratemyprofessors.com in Chrome\n"
                        "    2. DevTools -> Network -> filter 'graphql' -> click a request\n"
                        "    3. Headers -> Request Headers -> copy the 'cookie' value\n"
                        "    4. Save to cookie.txt and re-run\n"
                    )
                resp.raise_for_status()
                return resp.json()
            except RuntimeError:
                raise
            except requests.RequestException as exc:
                if attempt == retries - 1:
                    raise
                log.warning("Request failed (%s), retrying in %ds...", exc, 2 ** attempt)
                time.sleep(2 ** attempt)

    def fetch_school(self, school_id: str) -> dict | None:
        gql_id = base64.b64encode(f"School-{school_id}".encode()).decode()
        try:
            data = self._post(SCHOOL_QUERY, {"id": gql_id})
            node = data.get("data", {}).get("node")
            return node if node and node.get("name") else None
        except RuntimeError:
            raise
        except Exception as exc:
            log.debug("School %s not found: %s", school_id, exc)
            return None

    def fetch_professors_for_school(self, school_id: str) -> list[dict]:
        """Paginate through all professors. schoolID must be the base64 global ID."""
        professors = []
        cursor = None
        gql_school_id = base64.b64encode(f"School-{school_id}".encode()).decode()
        while True:
            variables = {
                "count": PROF_PAGE_SIZE,
                "cursor": cursor,
                "query": {"schoolID": gql_school_id},
            }
            try:
                data = self._post(PROFESSORS_QUERY, variables)
                teachers_data = (
                    data.get("data", {})
                        .get("search", {})
                        .get("teachers", {})
                )
                if not teachers_data:
                    break
                for edge in teachers_data.get("edges", []):
                    node = edge.get("node")
                    if node:
                        professors.append(node)
                page_info = teachers_data.get("pageInfo", {})
                if not page_info.get("hasNextPage"):
                    break
                cursor = page_info.get("endCursor")
                time.sleep(SLEEP_BETWEEN_REQUESTS)
            except RuntimeError:
                raise
            except Exception as exc:
                log.warning("Error paginating professors for school %s: %s", school_id, exc)
                break
        return professors

    def fetch_professor_ratings(self, gql_id: str) -> dict | None:
        try:
            data = self._post(PROFESSOR_RATINGS_QUERY, {"id": gql_id})
            return data.get("data", {}).get("node")
        except RuntimeError:
            raise
        except Exception as exc:
            log.warning("Error fetching professor %s: %s", gql_id, exc)
            return None


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _is_us_school(school: dict) -> bool:
    country = (school.get("country") or "").upper()
    state = school.get("state") or ""
    return "US" in country or bool(state)


def _school_location(school: dict) -> str:
    parts = [school.get("city"), school.get("state"), school.get("country")]
    return ", ".join(p for p in parts if p)


def _build_rows(prof_detail: dict, school_name: str,
                school_id: str, school_location: str) -> list[dict]:
    first = prof_detail.get("firstName", "") or ""
    last = prof_detail.get("lastName", "") or ""
    name = f"{first} {last}".strip()

    base = dict(
        professor_name             = name,
        school_name                = school_name,
        school_id                  = school_id,
        school_location            = school_location,
        department                 = prof_detail.get("department", ""),
        professor_avg_rating       = prof_detail.get("avgRating"),
        professor_avg_difficulty   = prof_detail.get("avgDifficulty"),
        professor_num_ratings      = prof_detail.get("numRatings", 0),
        professor_would_take_again = prof_detail.get("wouldTakeAgainPercent", -1),
    )

    rating_edges = prof_detail.get("ratings", {}).get("edges", [])
    eval_dicts = []
    for edge in rating_edges:
        node = edge.get("node", {})
        eval_dicts.append({
            "date":       node.get("date", ""),
            "class":      node.get("class", ""),
            "helpful":    node.get("helpfulRating"),
            "clarity":    node.get("clarityRating"),
            "difficulty": node.get("difficultyRating"),
            "comment":    node.get("comment", ""),
        })

    base["all_evaluations_json"] = json.dumps(eval_dicts, ensure_ascii=False)

    if not eval_dicts:
        return [{
            **base,
            "evaluation_date": None, "evaluation_class": None,
            "evaluation_helpful": None, "evaluation_clarity": None,
            "evaluation_difficulty": None, "evaluation_rating": None,
            "evaluation_comment": None,
        }]

    rows = []
    for ev in eval_dicts:
        helpful, clarity = ev.get("helpful"), ev.get("clarity")
        try:
            composite = round((float(helpful) + float(clarity)) / 2, 2)
        except (TypeError, ValueError):
            composite = None
        rows.append({
            **base,
            "evaluation_date":       ev.get("date"),
            "evaluation_class":      ev.get("class"),
            "evaluation_helpful":    helpful,
            "evaluation_clarity":    clarity,
            "evaluation_difficulty": ev.get("difficulty"),
            "evaluation_rating":     composite,
            "evaluation_comment":    ev.get("comment"),
        })
    return rows


# ---------------------------------------------------------------------------
# Core scraping
# ---------------------------------------------------------------------------

def scrape_school(client: RMPClient, school_id: str,
                  skip_no_ratings: bool = False) -> list[dict]:
    school = client.fetch_school(school_id)
    if not school:
        log.debug("School %s: not found.", school_id)
        return []
    if not _is_us_school(school):
        log.debug("School %s (%s): not a US school - skipping.",
                  school_id, school.get("name", "?"))
        return []

    school_name     = school.get("name", school_id)
    school_location = _school_location(school)
    log.info("Scraping: %s (ID=%s)  %s", school_name, school_id, school_location)

    professors = client.fetch_professors_for_school(school_id)
    log.info("  Found %d professors", len(professors))

    rows = []
    for prof in tqdm(professors, desc=school_name[:40], leave=False):
        if skip_no_ratings and (prof.get("numRatings") or 0) == 0:
            continue
        gql_id = prof.get("id")
        if not gql_id:
            continue
        detail = client.fetch_professor_ratings(gql_id)
        if detail:
            rows.extend(_build_rows(detail, school_name, school_id, school_location))
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    log.info("  -> %d rows collected for %s", len(rows), school_name)
    return rows


def build_dataframe(school_ids: list[str], cookie: str,
                    skip_no_ratings: bool = False,
                    workers: int = 1,
                    output_prefix: str = "rmp_evaluations") -> pd.DataFrame:

    # Resume from checkpoint if one exists
    completed_ids, all_rows = load_checkpoint(output_prefix)
    remaining = [sid for sid in school_ids if sid not in completed_ids]

    if completed_ids:
        log.info("Skipping %d already-completed schools. %d remaining.",
                 len(completed_ids), len(remaining))

    if not remaining:
        log.info("All schools already completed - building DataFrame from checkpoint.")
    elif workers <= 1:
        client = RMPClient(cookie)
        for i, sid in enumerate(remaining, 1):
            log.info("Progress: %d / %d schools  (ID=%s)",
                     len(completed_ids) + i, len(school_ids), sid)
            rows = scrape_school(client, sid, skip_no_ratings)
            all_rows.extend(rows)
            save_checkpoint(output_prefix, sid, rows)   # save after every school
    else:
        def _worker(sid):
            c = RMPClient(cookie)
            return sid, scrape_school(c, sid, skip_no_ratings)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_worker, sid): sid for sid in remaining}
            done = 0
            for fut in as_completed(futures):
                done += 1
                try:
                    sid, rows = fut.result()
                    all_rows.extend(rows)
                    save_checkpoint(output_prefix, sid, rows)
                    log.info("Progress: %d / %d schools done",
                             len(completed_ids) + done, len(school_ids))
                except Exception as exc:
                    log.error("Error scraping school %s: %s", futures[fut], exc)

    if not all_rows:
        log.warning("No data collected - check your cookie or school IDs.")
        return pd.DataFrame(columns=COLUMN_ORDER)

    df = pd.DataFrame(all_rows)
    for col in COLUMN_ORDER:
        if col not in df.columns:
            df[col] = None
    df = df[COLUMN_ORDER]

    for col in ("professor_avg_rating", "professor_avg_difficulty",
                "evaluation_rating", "evaluation_helpful",
                "evaluation_clarity", "evaluation_difficulty"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["professor_num_ratings"] = (
        pd.to_numeric(df["professor_num_ratings"], errors="coerce")
        .fillna(0).astype(int)
    )
    df["evaluation_date"] = pd.to_datetime(
        df["evaluation_date"], errors="coerce", utc=True
    )
    return df


def save_outputs(df: pd.DataFrame, prefix: str) -> None:
    csv_path = Path(prefix).with_suffix(".csv")
    df.to_csv(csv_path, index=False)
    log.info("Saved CSV -> %s  (%d rows)", csv_path, len(df))
    try:
        parquet_path = Path(prefix).with_suffix(".parquet")
        df.to_parquet(parquet_path, index=False)
        log.info("Saved Parquet -> %s", parquet_path)
    except Exception as exc:
        log.warning("Parquet save failed (pip install pyarrow to enable): %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Scrape RateMyProfessor evaluations for US schools.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--schools", nargs="+", metavar="ID",
                       help="One or more numeric RMP school IDs.")
    group.add_argument("--all-schools", action="store_true",
                       help="Probe every school ID from 1 to 5999.")

    cookie_group = parser.add_mutually_exclusive_group(required=True)
    cookie_group.add_argument("--cookie", metavar="STRING",
                              help="Full cookie string pasted from Chrome DevTools.")
    cookie_group.add_argument("--cookie-file", metavar="FILE",
                              help="Path to a text file containing the cookie string.")

    parser.add_argument("--output", default="rmp_evaluations",
                        help="Output file prefix (no extension). Default: rmp_evaluations")
    parser.add_argument("--workers", type=int, default=1,
                        help="Parallel threads for school fetching. Default: 1")
    parser.add_argument("--skip-no-ratings", action="store_true",
                        help="Omit professors with zero ratings.")
    parser.add_argument("--reset", action="store_true",
                        help="Delete existing checkpoint and start from scratch.")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Only scrape the next N unfinished schools. "
                             "Great for doing a batch per day without specifying IDs.")

    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    cookie = (Path(args.cookie_file).read_text().strip()
              if args.cookie_file else args.cookie)

    if args.reset:
        clear_checkpoint(args.output)

    if args.all_schools:
        school_ids = [str(i) for i in range(1, 6000)]
        log.info("All-schools mode: probing 5999 IDs.")
    else:
        school_ids = args.schools
        log.info("Scraping %d school(s): %s", len(school_ids), school_ids)

    # --limit: slice down to only the next N schools not yet in the checkpoint
    if args.limit is not None:
        completed_ids, _ = load_checkpoint(args.output)
        remaining = [sid for sid in school_ids if sid not in completed_ids]
        school_ids = remaining[: args.limit]
        log.info("--limit %d: will scrape schools %s",
                 args.limit, school_ids if len(school_ids) <= 10 else
                 f"{school_ids[:3]} ... {school_ids[-3:]} ({len(school_ids)} total)")

    df = build_dataframe(
        school_ids=school_ids,
        cookie=cookie,
        skip_no_ratings=args.skip_no_ratings,
        workers=args.workers,
        output_prefix=args.output,
    )

    log.info("DataFrame shape: %s", df.shape)
    print(df.head(10).to_string())
    save_outputs(df, args.output)

    # Checkpoint is no longer needed once the final CSV/Parquet is written
    clear_checkpoint(args.output)
    return df


if __name__ == "__main__":
    main()