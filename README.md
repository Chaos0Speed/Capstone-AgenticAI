# IITB Course RAG + Study Planner — Multi-Agent Capstone

**Agentic AI Learners' Space 2026 — Week 4 Capstone Project**
Laxmi Bhargav Mende (25B0940)

A supervisor-orchestrated multi-agent system that answers questions about IITB course material (PDFs, PPTs, Word docs, CSVs, scanned/handwritten notes) and schedules study sessions directly onto Google Calendar — all from a single natural-language query.

---

## Problem Statement

Course material at IITB is scattered across PDFs, slide decks, scanned notes, and handwritten pages, and students separately have to figure out *when* to study it around exam deadlines. Doing both — retrieving the right content **and** turning it into an actionable schedule — usually means switching between a search tool and a calendar app by hand.

This project collapses that into one system: ask it a question, and it either answers directly from your ingested course material (falling back to the web if the material doesn't cover it), or — if you're asking it to schedule something — it drafts a calendar event, shows it to you for approval, and pushes it to Google Calendar.

---

## Why a Supervisor Pattern

The system needs to route between two fundamentally different jobs — **answering** vs. **scheduling** — which have no shared sub-steps, so a single linear pipeline doesn't fit. A **Supervisor** node classifies user intent up front and dispatches to one of two independent sub-flows, each of which has its own internal conditional routing:

```
                              ┌──────────────┐
                              │  Supervisor  │
                              │ (classifies  │
                              │   intent)    │
                              └──────┬───────┘
                     informational   │   planning
                    ┌────────────────┴────────────────┐
                    ▼                                 ▼
            ┌───────────────┐                 ┌───────────────┐
            │  RAG Analyst  │                 │   Scheduler   │
            │ (2-stage RAG) │                 │ (extract event │
            └───────┬───────┘                 │    details)   │
                     │                         └───────┬───────┘
           rag_failed│rag_ok                           │
                     ▼                                 ▼
            ┌───────────────┐                 ┌───────────────┐
            │  Web Search   │                 │ Schedule Saver│
            │   (fallback)  │                 │(human approval)│
            └───────┬───────┘                 └───────┬───────┘
                     │                       rejected  │  approved
                     │                     ┌────────────┴────────┐
                     │                     ▼                     ▼
                     │              (skip integration)   ┌───────────────┐
                     │                                   │  Apply Plan   │
                     │                                   │ (Google Cal.  │
                     │                                   │     push)     │
                     │                                   └───────┬───────┘
                     └────────────────┬────────────────┬─────────┘
                                       ▼
                              ┌─────────────────┐
                              │ Final Synthesizer│
                              └─────────────────┘
```

This satisfies the assignment's four-pattern requirement (Supervisor) while nesting a **Pipeline**-style fallback chain inside each branch — RAG → web search on the informational side, and scheduler → human approval → calendar push on the planning side.

---

## Agents & Nodes

| Node | Role |
|---|---|
| **Supervisor** | Classifies the user's query as `informational` or `planning` using a structured-output LLM call, then routes accordingly. |
| **RAG Analyst** | Runs the two-stage retrieval + answer generation described in [Multi-Step RAG Architecture](#multi-step-rag-architecture) below, and flags `rag_failed` if the answer isn't grounded in the retrieved context. |
| **Web Search** | Fallback agent (ReAct-style, `DuckDuckGoSearchRun` tool) that activates only when the RAG analyst can't answer from ingested material. Cites source URLs. |
| **Scheduler** | Extracts structured event details (title, description, start/end time in IST, location) from the user's request via structured output, and builds a valid `.ics` block. |
| **Schedule Saver** | **Human-in-the-loop checkpoint.** Displays the proposed event and asks for approval (with a timeout-and-default fallback) before anything touches the calendar. |
| **Apply Plan** | Pushes the approved event to Google Calendar via the Calendar API. Skipped entirely if the user rejected the plan. |
| **Final Synthesizer** | Single exit point for the graph — reads the accumulated message history from whichever branch ran and produces the final user-facing answer. |

---

## Ingestion Pipeline (pre-processing, outside the graph)

Course material dropped into `input_files/` is processed once, ahead of time, into clean `.txt` files + a semantic description index used by the RAG analyst. Three working directories manage this:

- `input_files/` — raw, unprocessed source material (PDFs, PPTs, DOCX, CSV, images, subfolders).
- `run_files/` — normalized plain-text output, one `.txt` per source file, plus `descriptions.txt`.
- `.converted_files/` — archive of successfully processed originals, so re-running ingestion never reprocesses a file twice.

### File Parsing — per-format extraction with LLM fallback

Every file goes through `extract_file_content()`, which routes by **MIME type detected from file magic bytes** (the first few bytes of the file), not just the extension — this catches mislabeled or renamed files that a naive `.suffix` check would get wrong:

| Detected type | Extractor | Library |
|---|---|---|
| `application/pdf` | `extract_pdf()` — page-by-page text extraction | `pdfplumber` |
| `application/zip` + `.docx`/`.doc` | `extract_docx()` — paragraph text | `python-docx` |
| `application/zip` + `.pptx`/`.ppt` | `extract_pptx()` — text from every shape on every slide | `python-pptx` |
| `.csv` | `extract_csv()` — rows joined with `\|` as a pseudo-table | `csv` (stdlib) |
| `.txt` | `extract_txt()` — direct read | stdlib |
| anything else (images, scanned PDFs, handwritten notes, unrecognized ZIP contents) | `llm_fallback_extraction()` | Gemini file upload + vision |

**LLM fallback extraction** is triggered in three situations, not just "unsupported format":
1. The MIME/extension combination isn't recognized at all.
2. Native parsing throws an exception.
3. Native parsing *succeeds* but returns suspiciously little text (`< 200 chars`, `min_valid_chars` threshold) — this is what catches scanned/image-only PDF pages and diagram-heavy slides that technically "parsed" but yielded nothing useful.

When triggered, the file is uploaded directly to Gemini (`file_client.files.upload`), polled until processing finishes, then sent through a strict extraction system prompt instructing the model to output clean Markdown: preserve headers/bullets/bold, transcribe equations and tables properly, strip page numbers and repeated headers/footers, and emit **only** the extracted content with no conversational filler. This single fallback path is what makes handwritten notes, scanned slides, and any oddball format work — rather than maintaining a separate OCR pipeline, everything unsupported collapses into "let the vision model read it."

After extraction, `normalize_extracted_text()` runs a cleanup pass that's specific to PDF/PPT line-wrapping artifacts:
- Rejoins hyphenated words split across a line break (`"orga-\nnization"` → `"organization"`)
- Collapses accidental mid-sentence line breaks into spaces, while leaving real paragraph breaks (`\n\n`) and sentence-ending breaks intact
- Squashes any double spaces left behind by the merge

### `descriptions.txt` — the semantic index

Alongside every normalized `.txt` file, `generate_file_description()` makes one more LLM call: read the file's content (capped at 20,000 chars) and produce a dense 3–5 sentence summary explicitly naming key topics, formulas, entities, and the overarching theme — written specifically to be a good embedding target, not a human-readable blurb.

That description is appended to a single flat file, `run_files/descriptions.txt`, in a simple repeating block format:
```
<filename>.txt
<3-5 sentence dense description>
~~~
<next filename>.txt
<description>
~~~
...
```
The `~~~` delimiter is what `retrive_top_files()` splits on to recover each `(filename, description)` pair at query time. This file is intentionally flat rather than a database — it's small enough (one entry per source document, not per chunk) that reading and re-embedding it on every query is cheap, and it keeps the whole index human-inspectable/editable in a text editor.

A manual override path (`process_files()`) lets you hand-write a file's description instead of generating one via LLM — useful when you want tighter control over how a specific document gets matched (e.g. forcing a lecture-heavy match on a keyword the LLM's summary might not have emphasized).

---

## Multi-Step RAG Architecture

Retrieval happens in **two stages** rather than embedding and searching every chunk of every document up front — this keeps the search space small and relevant before the expensive part (chunk-level embedding) happens.

**Stage 1 — File-level retrieval (`retrive_top_files`)**
1. Read and parse `descriptions.txt` into a list of `(filename, description)` pairs.
2. Embed every description with `BAAI/bge-small-en-v1.5`.
3. Embed the user's query with the same model.
4. Cosine-similarity rank the descriptions against the query and return the top-k (default `k=10`) filenames.

This means the very first filter is "which *documents* are even plausibly relevant" — a query about ODEs never needs to touch a Psychology file's chunks at all, no matter how the chunk-level embeddings might coincidentally score.

**Stage 2 — Chunk-level retrieval (`retrieve_top_chunks`)**
1. Read only the shortlisted files from Stage 1 (not the whole corpus) from `run_files/`.
2. Apply **dynamic sentence chunking** to each: split on sentence delimiters (`. ! ? \n\n`), then group into chunks of **10 sentences with a 2-sentence overlap** between consecutive chunks — the overlap prevents a fact from being silently cut in half at a chunk boundary.
3. Embed every chunk (clean text only, no metadata) for the similarity comparison.
4. Separately keep a *formatted* copy of each chunk with its source filename prepended, so the version that reaches the LLM carries citation info even though the version used for embedding doesn't (metadata in the embedding text would just add noise to the similarity score).
5. Cosine-similarity rank all chunks from the shortlisted files against the query and return the top-k (default `k=10`).

**Stage 3 — Answer generation (`rag_analyst_node`)**
The top chunks are concatenated into a single context block and passed to the LLM with a strict instruction: answer using *only* the provided context, cite sources with `[Source: filename.txt]` tags, and output the exact string `I DO NOT KNOW` if the answer isn't in the context at all.

**Stage 4 — Failure detection**
Rather than string-matching for `"I DO NOT KNOW"` (fragile against minor rephrasing by the LLM), the response is embedded and compared via cosine similarity against the embedding of `"I DO NOT KNOW"` itself. A similarity above `0.8` flags `rag_failed = True`, which routes execution to the web-search fallback agent instead of returning a dead-end answer to the user.

This two-stage design (documents → chunks, rather than one flat chunk index over the whole corpus) is what keeps retrieval accurate as the course-material folder grows — adding more subjects doesn't dilute retrieval quality for any one subject, since Stage 1 already narrows the field before Stage 2 ever runs.

---

## Failure Handling

Per the assignment's requirement of at least one failure-handling mechanism, this project implements all three:

- **Retry / fallback extraction**: any parsing failure or suspiciously short extraction automatically retries via the LLM vision fallback rather than giving up on the file.
- **Fallback agent**: RAG failure (detected via embedding similarity to "I DO NOT KNOW") routes to a web-search agent rather than returning a dead end.
- **Human-in-the-loop checkpoint**: no calendar event is ever created without explicit user approval, with a timeout defaulting to "no" if the user doesn't respond — fails safe rather than silently scheduling something unintended.

---

## State Schema

```python
class AgenticState(TypedDict):
    # Core
    user_prompt: str
    messages: Annotated[List, add_messages]
    intent_category: str            # "informational" | "planning"

    # RAG & Search flow
    selected_files: List[str]
    retrieved_chunks: List[str]
    source_citations: List[str]
    rag_failed: bool
    answer: str

    # Planning flow
    calendar_ics_output: str
    user_approved: bool
```

A single shared state object is used (rather than fully isolated per-agent scratchpads), since this is a two-branch supervisor pattern rather than a many-subagent hierarchical system — each node only reads/writes the keys relevant to its branch, keeping cross-talk minimal without the overhead of isolated state.

---

## Tech Stack

- **Orchestration**: LangGraph (`StateGraph`, conditional edges, `MemorySaver` checkpointing)
- **LLM**: Gemini (`langchain_google_genai`), via structured output (`.with_structured_output`) for routing, event extraction, and RAG intent classification
- **Embeddings**: `BAAI/bge-small-en-v1.5` (`sentence-transformers`)
- **File parsing**: `pdfplumber`, `python-docx`, `python-pptx`, `csv`
- **Web search fallback**: `DuckDuckGoSearchRun` (LangChain community tools)
- **Calendar integration**: Google Calendar API (`google-api-python-client`, OAuth2 via `google-auth`)
- **ICS handling**: `icalendar`

---

## Setup

```bash
pip install langgraph langchain langchain-google-genai langchain-community \
            sentence-transformers pdfplumber python-docx python-pptx \
            icalendar google-api-python-client google-auth-httplib2 google-auth-oauthlib \
            duckduckgo-search python-dotenv
```

1. Create a `.env` file with:
   ```
   GEMINI_API_KEY=your_key_here
   ```
2. Set up Google Calendar OAuth credentials and save the resulting token as `token.json` in the project root (scope: `https://www.googleapis.com/auth/calendar.events`).
3. Place course material into `input_files/` (subfolders supported — used as filename prefixes to avoid collisions).
4. Run the ingestion cells to populate `run_files/` and `descriptions.txt`.
5. Run the graph via `run_app("your query here")`.

---

## Example Queries

```python
queries = [
    "Explain what the core fundamentals of a business are.",       # RAG path
    "Who won the FIFA World Cup in 2026?",                          # RAG fails -> web search fallback
    "Schedule a 1-hour Capstone Project sync with my advisor today at 5 PM.",  # Planning path
    "Block my calendar for a study session on Friday morning.",     # Planning path
]
```

---

## Known Limitations / Future Work

- Approval detection in `planner_saver` uses embedding similarity against a fixed "yes" phrase rather than a dedicated intent classifier — works well for clear yes/no responses but hasn't been stress-tested against ambiguous replies (e.g. "yes but change the time").
- The RAG failure detector uses the same embedding-similarity approach against "I DO NOT KNOW" — a small labeled eval set would make the 0.8 threshold more defensible.
- Currently a two-branch Supervisor rather than a full Hierarchical multi-subagent system; a natural extension would be splitting the scheduler into separate planner (what to study) and scheduler (when) agents, as originally scoped, with a dedicated mailer agent reusing the Week 3 Gmail MCP work for daily reminders.
- Single calendar event per scheduling request — no batch/recurring event support yet.
