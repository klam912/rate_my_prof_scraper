# RateMyProfessors Web Scraper
### `rmp_scraper.py` — Technical Documentation

---

## Table of Contents

1. [Overview](#overview)
2. [Background: How the Data is Accessed](#background-how-the-data-is-accessed)
3. [Data Collected](#data-collected)
4. [Installation & Requirements](#installation--requirements)
5. [Authentication](#authentication)
6. [Usage](#usage)
7. [Resuming Interrupted Runs](#resuming-interrupted-runs)
8. [Output Files](#output-files)
9. [Function Reference](#function-reference)
10. [Design Decisions & Limitations](#design-decisions--limitations)

---

## Overview

`rmp_scraper.py` is a Python script that systematically collects professor evaluation data from [RateMyProfessors.com](https://www.ratemyprofessors.com) (RMP) for institutions located in the United States. RateMyProfessors is one of the largest publicly accessible repositories of student-submitted evaluations of college and university instructors in the United States, with data spanning thousands of institutions and millions of individual reviews.

The scraper collects data at two levels of granularity:

- **Professor level** — summary statistics for each professor (average rating, average difficulty, total number of ratings, and the percentage of students who would take the professor again).
- **Evaluation level** — every individual student review submitted for each professor, including the written comment, numeric scores, course code, and date of submission.

The final output is a structured table (saved as a `.csv` file) where **each row represents one individual student evaluation**, along with the professor and school information associated with that review. This row-per-evaluation structure makes the dataset directly suitable for statistical analysis, natural language processing of student comments, and longitudinal studies of evaluation trends.

---

## Background: How the Data is Accessed

RateMyProfessors does not offer a public API. However, the website communicates with its own backend server using a technology called **GraphQL** — a structured query language that allows a client (in this case, the scraper) to request precisely the fields of data it needs. The scraper sends requests to this internal endpoint in a format that mirrors how the RateMyProfessors website itself fetches data in a user's browser. To be recognized as a legitimate browser session, each request must include a **session cookie** — a small piece of identifying text that RateMyProfessors issues when a user visits the site. This cookie is obtained manually from a browser (see [Authentication](#authentication)) and passed to the script. Cookies expire periodically and must be refreshed.

To avoid overwhelming the RateMyProfessors servers, the scraper pauses for **300 milliseconds** between individual requests. This rate-limiting behavior is consistent with responsible web data collection practices.

---

## Data Collected

The output table contains the following columns for every student evaluation record:

| Column | Description |
|---|---|
| `professor_name` | Full name of the professor |
| `school_name` | Name of the institution |
| `school_id` | RateMyProfessors' internal numeric identifier for the school |
| `school_location` | City, state, and country of the institution |
| `department` | Academic department the professor belongs to |
| `professor_avg_rating` | Professor's overall average rating (1–5), as computed by RateMyProfessors across all reviews |
| `professor_avg_difficulty` | Professor's average difficulty score (1–5), as computed by RateMyProfessors |
| `professor_num_ratings` | Total number of ratings the professor has received |
| `professor_would_take_again` | Percentage of reviewers who indicated they would take the professor again (-1 if unavailable) |
| `evaluation_date` | Date and time the individual student review was submitted |
| `evaluation_class` | Course code associated with the review (e.g., `WRIT340`) |
| `evaluation_helpful` | Helpfulness score given in this review (1–5), sourced directly from RateMyProfessors |
| `evaluation_clarity` | Clarity score given in this review (1–5), sourced directly from RateMyProfessors |
| `evaluation_difficulty` | Difficulty score given in this review (1–5), sourced directly from RateMyProfessors |
| `evaluation_rating` | **Computed field**: the average of `evaluation_helpful` and `evaluation_clarity` for this review, providing a single per-evaluation quality score |
| `evaluation_comment` | The full text of the student's written comment |
| `all_evaluations_json` | A complete JSON-formatted record of all evaluations for the professor, stored in each row for reference |

> **Note on computed vs. sourced fields:** All numeric fields except `evaluation_rating` are retrieved directly from RateMyProfessors and reflect the values displayed on the website. The field `evaluation_rating` is the only value calculated by this script, computed as the arithmetic mean of `evaluation_helpful` and `evaluation_clarity`.

---

## Installation & Requirements

The script requires **Python 3.10 or later**. Install the required libraries with:

```bash
pip install requests pandas tqdm pyarrow
```

| Library | Purpose |
|---|---|
| `requests` | Sends HTTP requests to the RateMyProfessors GraphQL API |
| `pandas` | Organizes collected data into a structured table and saves it as CSV/Parquet |
| `tqdm` | Displays a progress bar while scraping professors within a school |
| `pyarrow` | Enables saving output in Parquet format (optional but recommended for large datasets) |

---

## Authentication

Because RateMyProfessors requires a browser session to access its data, the script must be provided with a **session cookie** obtained from a real browser visit to the site. This cookie does not require a user account — it is issued automatically when any visitor loads the RateMyProfessors homepage.

**Steps to obtain the cookie:**

1. Open [ratemyprofessors.com](https://www.ratemyprofessors.com) in Google Chrome.
2. Open Chrome's Developer Tools by pressing `F12` (or `Cmd+Option+I` on Mac).
3. Click the **Network** tab at the top of the Developer Tools panel.
4. In the filter box, type `graphql` to show only relevant requests.
5. Click on any request that appears in the list.
6. In the panel that opens, click **Headers**, then scroll to **Request Headers**.
7. Find the field labeled `cookie` and copy its entire value.
8. Save this value to a plain text file named `cookie.txt`.

Session cookies typically expire after a few hours. If the scraper reports a `403 Forbidden` error, the cookie has expired and must be refreshed using the steps above.

---

## Usage

All commands are run from the terminal in the folder containing `rmp_scraper.py`.

**Scrape specific schools by their RateMyProfessors ID:**
```bash
python rmp_scraper.py --schools 389 1381 --cookie-file cookie.txt
```

**Scrape all US schools (IDs 1 through 5999):**
```bash
python rmp_scraper.py --all-schools --cookie-file cookie.txt
```

**Scrape a limited batch (e.g., 50 schools today, continue tomorrow):**
```bash
python rmp_scraper.py --all-schools --cookie-file cookie.txt --limit 50
```

**Skip professors who have received no ratings:**
```bash
python rmp_scraper.py --all-schools --cookie-file cookie.txt --skip-no-ratings
```

**Run with parallel threads to speed up collection:**
```bash
python rmp_scraper.py --all-schools --cookie-file cookie.txt --workers 4
```

**Full list of options:**

| Option | Description |
|---|---|
| `--schools ID [ID ...]` | Scrape one or more specific school IDs |
| `--all-schools` | Scrape all school IDs from 1 to 5999 |
| `--cookie-file FILE` | Path to a text file containing the session cookie |
| `--cookie STRING` | Session cookie pasted directly into the command |
| `--output PREFIX` | Base name for output files (default: `rmp_evaluations`) |
| `--limit N` | Only scrape the next N schools not yet completed |
| `--workers N` | Number of parallel threads (default: 1; recommended maximum: 4) |
| `--skip-no-ratings` | Omit professors with zero student reviews |
| `--reset` | Delete the checkpoint file and restart from scratch |

---

## Resuming Interrupted Runs

Because scraping thousands of schools can take many hours or days, the script saves progress automatically after each school is completed. This progress is stored in a file named `rmp_evaluations.checkpoint.jsonl` (or matching your `--output` prefix).

If the script is stopped for any reason — including a cookie expiry, a network interruption, or a deliberate stop — simply re-run the same command. The script will read the checkpoint file, skip all schools already completed, and continue from where it left off. Once all requested schools have been processed and the final output files are saved, the checkpoint file is automatically deleted.

To discard all prior progress and start fresh:
```bash
python rmp_scraper.py --all-schools --cookie-file cookie.txt --reset
```

---

## Output Files

Upon completion, the script writes two output files:

- **`rmp_evaluations.csv`** — A comma-separated values file readable in Excel, R, Python, SPSS, or any other data analysis tool. Each row is one student evaluation.
- **`rmp_evaluations.parquet`** — A compressed binary format optimized for large datasets. Recommended when working with data from many schools, as it loads significantly faster than CSV in Python and R.

Both files contain identical data. The file name prefix can be changed with the `--output` option.

---

## Function Reference

The script is organized into four logical sections: checkpoint management, the API client, data transformation helpers, and the main scraping orchestration. Each function is described below.

---

### Checkpoint Management

These functions handle saving and loading progress so that long-running scraping jobs can be interrupted and resumed.

---

#### `checkpoint_path(output_prefix)`

Returns the file path for the checkpoint file, derived from the output prefix. For example, an output prefix of `rmp_evaluations` produces a checkpoint path of `rmp_evaluations.checkpoint.jsonl`. The `.jsonl` format (JSON Lines) stores one school's worth of data per line, making it easy to append incrementally without rewriting the entire file.

---

#### `load_checkpoint(output_prefix)`

Reads the checkpoint file (if one exists) and returns two things: the set of school IDs already completed, and all the data rows collected so far. If no checkpoint file exists, it returns empty values and the scraper starts fresh. If a checkpoint file is found, the script logs how many schools have already been completed and how many rows have been recovered. Corrupt lines in the checkpoint file are skipped with a warning rather than causing the script to crash.

---

#### `save_checkpoint(output_prefix, school_id, rows)`

Appends a single school's completed data to the checkpoint file immediately after that school finishes scraping. This ensures that even if the script is interrupted mid-run, no completed school's data is lost. Each entry written to the file contains the school's ID and the full list of evaluation rows collected from it.

---

#### `clear_checkpoint(output_prefix)`

Deletes the checkpoint file. This is called automatically once the final CSV and Parquet files have been successfully written, since the checkpoint is no longer needed. It can also be triggered manually via the `--reset` command-line flag.

---

### API Client (`RMPClient`)

This class manages all communication with the RateMyProfessors GraphQL API. An instance of `RMPClient` holds an authenticated HTTP session that is reused across requests, which is more efficient than opening a new connection for each query.

---

#### `RMPClient.__init__(cookie)`

Initializes the HTTP session and attaches the browser session cookie and standard request headers to every outgoing request. The headers are set to mimic a real Chrome browser, which is necessary for the RateMyProfessors server to accept the requests.

---

#### `RMPClient._post(query, variables, retries=3)`

The core method that sends a single GraphQL query to the RateMyProfessors API and returns the response. If a request fails due to a temporary network error, it retries up to three times with progressively longer waits between attempts (1 second, then 2 seconds, then 4 seconds). If the server returns a `403 Forbidden` response, the method raises an error with clear instructions for refreshing the session cookie, since this error specifically indicates that the cookie has expired.

---

#### `RMPClient.fetch_school(school_id)`

Retrieves basic information about a school given its numeric RateMyProfessors ID. The numeric ID must first be converted to an encoded format (Base64) that the GraphQL API expects — for example, school ID `389` becomes `U2Nob29sLTM4OQ==`. The method returns the school's name, city, state, and country, or returns nothing if the ID does not correspond to a school in the RateMyProfessors database.

---

#### `RMPClient.fetch_professors_for_school(school_id)`

Retrieves the full list of professors associated with a given school. Because RateMyProfessors returns professors in pages of 20 at a time, this method automatically fetches additional pages until all professors have been retrieved (a process called **pagination**). Each professor's summary information — name, department, average rating, and total number of ratings — is collected at this stage. The school ID is converted to Base64 encoding before being sent to the API, as this is required by the RateMyProfessors GraphQL schema.

---

#### `RMPClient.fetch_professor_ratings(gql_id)`

Retrieves the full detail record for a single professor, including all individual student evaluations (up to 1,000 per professor). This is called once per professor after their basic information has been retrieved. The returned data includes each review's written comment, helpfulness score, clarity score, difficulty score, course code, and submission date.

---

### Data Transformation Helpers

These functions convert raw API responses into structured rows suitable for the output table.

---

#### `_is_us_school(school)`

Determines whether a school is located in the United States by checking its country and state fields. Only US institutions are included in the output. Schools are identified as US-based if their country field contains "US" or if a US state abbreviation is present. This filter is necessary because RateMyProfessors includes institutions from Canada and other countries in addition to the United States.

---

#### `_school_location(school)`

Constructs a human-readable location string (e.g., `Clinton, NY, 0-US-United States`) by combining the city, state, and country fields returned by the API.

---

#### `_build_rows(prof_detail, school_name, school_id, school_location)`

Transforms a professor's full detail record into one or more table rows, one row per student evaluation. Professor-level fields (name, department, average rating, etc.) are copied into every row so that the output table is self-contained and each row carries complete context. If a professor has no evaluations, a single row is still created with the professor's summary information and blank evaluation fields, ensuring the professor appears in the dataset. The `evaluation_rating` composite score is computed here as the average of the helpfulness and clarity scores. All evaluations for the professor are also serialized to JSON and stored in the `all_evaluations_json` column for reference.

---

### Scraping Orchestration

These functions coordinate the overall data collection process.

---

#### `scrape_school(client, school_id, skip_no_ratings=False)`

Orchestrates the complete data collection for a single school. It first verifies that the school exists and is located in the United States, then retrieves the full professor list, and then fetches individual evaluation data for each professor. A progress bar is displayed in the terminal while professors are being processed. If `skip_no_ratings` is enabled, professors with zero reviews are silently skipped. The function returns a flat list of all evaluation rows collected from the school.

---

#### `build_dataframe(school_ids, cookie, skip_no_ratings, workers, output_prefix)`

The central function that manages the entire scraping run across all requested schools. It first checks for an existing checkpoint and skips any schools already completed. It then iterates through the remaining schools, calling `scrape_school` for each one and saving progress to the checkpoint file after every school. If `workers` is greater than 1, schools are scraped in parallel using multiple threads, which reduces total runtime but increases the rate of requests to the server. Once all schools are processed, the collected rows are assembled into a Pandas DataFrame with consistent column types and returned.

---

#### `save_outputs(df, prefix)`

Saves the completed DataFrame to disk in two formats: CSV (universally readable) and Parquet (compressed, efficient for large datasets). The file names are derived from the `--output` prefix.

---

#### `parse_args(argv=None)`

Parses all command-line arguments provided when the script is run. Defines and validates the full set of options described in the [Usage](#usage) section.

---

#### `main(argv=None)`

The entry point of the script. It reads the command-line arguments, loads the session cookie, optionally resets the checkpoint, determines the list of school IDs to scrape (applying the `--limit` filter if specified), calls `build_dataframe` to execute the scraping run, prints the first 10 rows of the result to the terminal, saves the output files, and deletes the checkpoint file.

---

## Design Decisions & Limitations

**Row-per-evaluation structure.** Each row in the output corresponds to a single student review rather than a single professor. This means professor-level fields such as `professor_avg_rating` are repeated across multiple rows for the same professor. This design was chosen because it makes the dataset immediately usable for analysis of individual reviews (e.g., sentiment analysis of comments, regression of individual scores) without requiring a secondary join or unnesting step.

**US-only filtering.** The scraper checks each school's location and discards non-US institutions before fetching any professor data. This avoids collecting data that falls outside the intended research scope and reduces unnecessary API requests.

**School ID range.** RateMyProfessors assigns numeric IDs to schools sequentially. When `--all-schools` is used, the scraper probes IDs 1 through 5,999. Not all IDs correspond to active schools — many return empty results and are silently skipped.

**Session cookie expiry.** The authentication mechanism relies on a short-lived browser session cookie. For very long scraping runs, the cookie may expire mid-run, causing a `403 Forbidden` error. When this happens, the scraper halts and displays instructions for obtaining a new cookie. Because progress is checkpointed after every school, no data is lost and the run can be resumed with a fresh cookie.

**Maximum evaluations per professor.** The script requests up to 1,000 individual reviews per professor in a single API call. Professors with more than 1,000 reviews will have their oldest reviews silently truncated. In practice, very few professors on RateMyProfessors accumulate more than 1,000 reviews, so this limit is unlikely to affect most records.

**Rate limiting.** A 300-millisecond pause is enforced between requests. This reduces the risk of the script being blocked by RateMyProfessors' servers and constitutes a reasonable level of courtesy toward the platform. Increasing the number of parallel workers (`--workers`) proportionally increases the request rate and should be done cautiously.