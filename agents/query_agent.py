"""
Query Agent — answers natural language questions about listings in the database.

Uses Text-to-SQL: the LLM generates a SELECT query, runs it via the query_db tool,
then returns a plain-English answer. No vector embeddings needed — the data is
fully structured and SQL is the right retrieval mechanism.

Called by the Next.js API route with the question passed as a CLI argument.
Prints a single JSON object to stdout: {answer, sql, rows}.
"""
import json
import os
import re
import sys
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env.local"))

from openai import OpenAI
from tools.db import fetch_all

MODEL = "deepseek/deepseek-v4-flash"

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)

SCHEMA = """
Database schema (PostgreSQL):

listings
  id uuid PK
  source text          -- 'cargurus' | 'carscom' | 'carmax' | 'carvana' | 'dealer'
  condition text       -- 'new' | 'used'
  make text, model text, trim text, year int
  body_type text       -- 'sedan' | 'suv' | 'crossover'
  mileage int, price int
  dealer_name text, dealer_phone text, listing_url text
  status text          -- 'new'|'active'|'contacted'|'dismissed'|'purchased'
  title_status text    -- 'clean'|'salvage'|'rebuilt'|'flood'|'lemon'|'unknown'
  accident_count int
  is_pinned bool
  first_seen_at timestamptz, last_seen_at timestamptz

listing_scores
  listing_id uuid FK → listings.id
  value_score numeric       -- 0-100; pure price deviation from ML fair value
  market_median_price int   -- ML-predicted fair price (encodes year/mileage/trim/drivetrain/powertrain)
  market_price_source text  -- 'ml_model'

contact_log
  id uuid PK
  listing_id uuid FK → listings.id
  contacted_at timestamptz
  channel text         -- 'contact_form'|'phone'|'other'
  note text            -- freeform notes the user added

Common join pattern:
  SELECT l.*, ls.value_score, ls.market_median_price, ls.market_price_source, ls.shap_values
  FROM listings l
  LEFT JOIN listing_scores ls ON ls.listing_id = l.id
"""

SYSTEM = f"""You are a car-deal assistant for a personal car shopping tracker called CarHunt.
You have read-only access to the user's listings database via the query_db tool.

{SCHEMA}

Rules:
- Only run SELECT queries. Never UPDATE, INSERT, DELETE, or DROP.
- Limit results to 20 rows unless the user explicitly asks for more.
- When listing cars, always include make, model, year, price, mileage, and value_score.
- Be concise. Answer in 1-3 sentences max, then list results if relevant.
- If a query returns no rows, say so plainly.
- Use plain dollars (e.g. "$24,500") not raw numbers when talking about prices.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_db",
            "description": "Run a read-only SELECT query against the CarHunt listings database.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A valid PostgreSQL SELECT statement.",
                    }
                },
                "required": ["sql"],
            },
        },
    }
]

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def _query_db(sql: str) -> str:
    sql = sql.strip()
    if not sql.upper().startswith("SELECT"):
        return json.dumps({"error": "Only SELECT queries are permitted."})
    if _FORBIDDEN.search(sql):
        return json.dumps({"error": "Query contains forbidden keywords."})
    try:
        rows = fetch_all(sql)
        return json.dumps([dict(r) for r in rows[:20]], default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _assistant_msg(msg) -> dict:
    d: dict = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
    return d


def run(question: str) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": question},
    ]

    last_sql = None
    last_rows = []

    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
        )
        msg = response.choices[0].message
        messages.append(_assistant_msg(msg))

        if response.choices[0].finish_reason != "tool_calls" or not msg.tool_calls:
            return {
                "answer": msg.content or "",
                "sql": last_sql,
                "rows": last_rows,
            }

        for tc in msg.tool_calls:
            inputs = json.loads(tc.function.arguments)
            result = _query_db(inputs["sql"])
            last_sql = inputs["sql"]
            try:
                last_rows = json.loads(result)
                if isinstance(last_rows, dict):  # error dict
                    last_rows = []
            except Exception:
                last_rows = []
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No question provided"}))
        sys.exit(1)
    question = " ".join(sys.argv[1:])
    result = run(question)
    print(json.dumps(result, default=str))
