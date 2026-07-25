"""
Vercel serverless function: POST /api/generate

Body: { "stage": str, "theme": str, "num_topics": int, ...stage-specific context }
Header: Authorization: Bearer <supabase access token>

This runs the same 5-stage pipeline that used to live in
streamlit_version/generator.py (Topic Planner -> Topic Researcher ->
Summary Generator -> Link Collector -> Article Prompt Writer), with the
exact same role/goal/backstory/task text for each stage.

Two changes from the original CrewAI version:
1. It no longer uses the `crewai` library. CrewAI's own dependency tree
   (chromadb, onnxruntime, langchain, a kubernetes client, etc.) is
   ~800MB by itself regardless of which features you use, and Vercel
   hard-caps serverless functions at 500MB.
2. Each stage is now its own request instead of one request running all
   5 stages internally. A single Gemini call takes well under Vercel's
   60-second Hobby-plan function timeout, but 5 of them back-to-back in
   one invocation can exceed it. The frontend (app/page.tsx) now calls
   this endpoint once per stage and passes the previous stage's output
   back in as context - same sequential handoff, just orchestrated by
   the client instead of inside one long-running function.
"""

from http.server import BaseHTTPRequestHandler
import json
import os
import re
import time
import requests
import google.generativeai as genai

SUPABASE_URL = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")


def _load_api_keys():
    """Collects every configured Gemini key so requests can rotate across them.

    Supports either:
      - GOOGLE_API_KEY, GOOGLE_API_KEY_2, GOOGLE_API_KEY_3, GOOGLE_API_KEY_4
        (separate env vars), or
      - GOOGLE_API_KEYS as a comma-separated list.
    """
    keys = []

    combined = os.environ.get("GOOGLE_API_KEYS", "")
    if combined:
        keys.extend([k.strip() for k in combined.split(",") if k.strip()])

    for name in ("GOOGLE_API_KEY", "GOOGLE_API_KEY_2", "GOOGLE_API_KEY_3", "GOOGLE_API_KEY_4"):
        val = os.environ.get(name)
        if val and val not in keys:
            keys.append(val)

    return keys


API_KEYS = _load_api_keys()
_key_cursor = {"i": 0}  # module-level so it persists across requests on a warm instance


def _next_key_order():
    """Returns all configured keys starting from the next one in rotation,
    so consecutive requests spread across keys and a 429 can fall through
    to the next key within the same request."""
    if not API_KEYS:
        return []
    start = _key_cursor["i"] % len(API_KEYS)
    _key_cursor["i"] = (start + 1) % len(API_KEYS)
    return API_KEYS[start:] + API_KEYS[:start]


def verify_supabase_user(access_token: str):
    """Confirms the request carries a valid, logged-in Supabase session."""
    if not access_token or not SUPABASE_URL:
        return None
    resp = requests.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={
            "Authorization": f"Bearer {access_token}",
            "apikey": os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY", ""),
        },
        timeout=10,
    )
    if resp.status_code == 200:
        return resp.json()
    return None


class ArticleTopicGenerator:
    """Same 5-stage pipeline as streamlit_version/generator.py's CrewAI
    agents/tasks - each stage's role/goal/backstory/description/expected_output
    text is preserved verbatim, just run directly instead of through CrewAI,
    and exposed one stage at a time instead of all at once."""

    def __init__(self, api_key):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(
            "gemini-3.5-flash",
            generation_config={"temperature": 0.8},
        )

    def _run_stage(self, role, goal, backstory, description, expected_output, context):
        prompt = f"""You are acting as the "{role}" in a multi-step content pipeline.

Your goal: {goal}

Your backstory / working relationship with the rest of the pipeline: {backstory}

Instructions for this step:
{description}

Expected output: {expected_output}

{context}

Now produce your output. Follow the instructions and expected output format exactly, with no extra commentary before or after it."""
        response = self.model.generate_content(prompt)
        return response.text

    def plan(self, theme, number_of_topics):
        return self._run_stage(
            role="Topic Planner",
            goal=f"To collect {number_of_topics} engaging topics related to the theme: {theme}, addressed to an academic audience",
            backstory=f"You have been given a theme - {theme} - and you must collect {number_of_topics} topics related to the theme, for people to write articles about. It can be in-depth core topics related to the theme, or informatory topics as well. Your work is the basis for the user to write an article (college graduate level) on these topics.",
            description=f"""
            1. Identify the latest trends related to {theme}, along with key players and noteworthy news
            2. Identify the target audience based on {theme} and collect relevant headlines/topics
            3. Develop a {theme}-related title list of {number_of_topics} items
            4. Format the output as a numbered list with no additional commentary
            5. Example:
                1. Topic One
                2. Topic Two
                3. Topic Three
            6. Send the list to the Topic Researcher""",
            expected_output=f"A {number_of_topics}-item numbered list of {theme}-related topics with no extra text",
            context="",
        )

    def research(self, theme, number_of_topics, planner_output):
        return self._run_stage(
            role="Topic Researcher",
            goal=f"To collect in-depth information (and their sources) on the {number_of_topics} {theme}-related topics provided by the Topic Planner",
            backstory="For each topic given by the Topic Planner, you will do in-depth research into each, collect information and their source links, and send the links to the Link Collector. Also, you send the relevant informaton you have collected to the Summary Generator.",
            description="""
            For each topic received from the Topic Planner:
            1. Conduct in-depth research on the topic
            2. Use at least 5-6 sources
            3. Collect information and source links
            4. Format research content as:
                - Heading: "### Research Findings"
                - Bullet points with bolded subheadings
            5. Format source links as:
                - Heading: "### Source Links"
                - Numbered list of exact URLs
            6. Example:
                ### Research Findings
                - **Key Discovery:** Explanation of discovery
                - **Important Fact:** Detailed fact

            ### Source Links
            1. <exact link here>
            2. <exact link here>
            7. Send the research findings to the Summary Generator""",
            expected_output="Structured research findings with exact source links for all topics",
            context=f"Topics from the Topic Planner:\n{planner_output}",
        )

    def condense(self, research_output):
        return self._run_stage(
            role="Summary Generator",
            goal="To condense paragraphs of information into a title-one liner duo and show it to the user",
            backstory="You will take the information the Topic Researcher, and split it into small chunks. Then you will condense it into a bullet point-worth of information and title each of these bullets. The user will elaborate on each point, by themselves, as they see fit. This should be shown to the user under the title 'Condensed Information Points:'",
            description="""
            1. Receive research content from Topic Researcher
            2. For each logical chunk:
                a. Create a bolded heading (1-3 words)
                b. Add colon followed by 1-sentence summary
            3. Output as:
                - Heading: "### Condensed Information Points"
                - Bullet points with headings
            4. Do not add commentary
            5. Example:
                ### Condensed Information Points
                - **Brain-Computer Interface:** Direct pathway between brain and external devices
                - **Neural Signals:** BCIs interpret signals to control computers""",
            expected_output="Markdown section with bolded headings and colon-separated summaries",
            context=f"Research findings from the Topic Researcher:\n{research_output}",
        )

    def links(self, research_output):
        return self._run_stage(
            role="Link Collector",
            goal="To collect all the links of the material that were used as sources by the Topic Researcher",
            backstory="You will take all the links from the researcher, and show them to the user at the end of the response under the title: 'Resources Used:'",
            description="""
            1. Collect all source links from Topic Researcher
            2. Format as:
                - Heading: "### Resources Used"
                - Numbered list of exact URLs
            3. Preserve original link formatting
            4. Do not modify or shorten URLs
            5. Example:
                ### Resources Used
                1. https://www.nature.com/articles/bci-technology
                2. https://ieeexplore.ieee.org/document/123456""",
            expected_output="Numbered list of exact source URLs under heading",
            context=f"Research findings (containing the source links) from the Topic Researcher:\n{research_output}",
        )

    def write(self, theme, number_of_topics, planner_output, condensed_output, links_output):
        return self._run_stage(
            role="Article Prompt Writer",
            goal=f"To take each topic from the {number_of_topics} topics the Topic Planner has generated, give the condensed article prompt the Summary Generator has generated for the same, and then the links the Link Collector has collected for the same topic, and repeat the steps for the rest of the topics",
            backstory=f"The Topic Planner has sent {number_of_topics} topics to the Topic Researcher, who sent the information to the Summary Generator and the research links to the Link Collector, who have all sent their information chunks to you, who orders it and shows it to the user.",
            description=f"""
            For each of the {number_of_topics} topics:
            1. Start with H2 heading: "## [Topic Name]"
            2. Include condensed points from Summary Generator
            3. Include resource links from Link Collector
            4. Maintain exact formatting:
                ## Topic <Number>: <Topic Title>

                ### Condensed Information Points
                - **heading:** summary (from condenser / Summary Generator)

                ### Resources Used
                1. <exact link here>
            5. Do not add commentary or summaries""",
            expected_output="Structured output with headings, bullet points, and exact links for all topics",
            context=(
                f"Topics from the Topic Planner:\n{planner_output}\n\n"
                f"Condensed Information Points from the Summary Generator:\n{condensed_output}\n\n"
                f"Resources Used from the Link Collector:\n{links_output}"
            ),
        )


VALID_STAGES = {"plan", "research", "condense", "links", "write"}


def run_with_key_rotation(stage_method_name, *args, max_wait_seconds=25):
    """Tries every configured Gemini key for this stage. If a key is out of
    quota (429), immediately falls through to the next key with no delay.

    The timer for the wait starts at the FIRST 429 in this pass (not after
    every key has been tried), so by the time all keys have been exhausted,
    some of Google's suggested retry_delay has usually already elapsed just
    from making those fallback calls. We only sleep the remainder.
    """
    key_order = _next_key_order()
    if not key_order:
        raise ValueError(
            "No Gemini API key configured (GOOGLE_API_KEY / GOOGLE_API_KEY_2 / "
            "GOOGLE_API_KEY_3 / GOOGLE_API_KEY_4)."
        )

    def _try_all_keys():
        last_err = None
        timer_start = None
        for key in key_order:
            try:
                generator = ArticleTopicGenerator(key)
                method = getattr(generator, stage_method_name)
                return True, method(*args), timer_start
            except Exception as e:
                msg = str(e)
                if "429" in msg or "quota" in msg.lower() or "rate limit" in msg.lower():
                    if timer_start is None:
                        timer_start = time.monotonic()  # start the clock on the first 429
                    last_err = e
                    continue
                raise
        return False, last_err, timer_start

    ok, result, timer_start = _try_all_keys()
    if ok:
        return result

    # Every key failed. Google told us roughly how long until quota frees up
    # (retry_delay); subtract the time we already spent trying the other
    # keys before we ever sleep.
    requested_delay = _parse_retry_delay_seconds(str(result), default=5, cap=max_wait_seconds)
    elapsed = time.monotonic() - timer_start if timer_start is not None else 0
    remaining = max(0.0, requested_delay - elapsed)
    if remaining > 0:
        time.sleep(remaining)

    ok, result, _ = _try_all_keys()
    if ok:
        return result

    raise result or Exception("All Gemini API keys are out of quota.")


def _parse_retry_delay_seconds(error_message, default=5, cap=25):
    """Pulls the seconds out of Google's `retry_delay { seconds: N }` field
    in the error message, falling back to `default` if it's not present."""
    match = re.search(r"retry_delay\s*{\s*seconds:\s*(\d+)", error_message)
    if match:
        return min(int(match.group(1)), cap)
    return default


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length) or b"{}")

        stage = body.get("stage")
        theme = (body.get("theme") or "").strip()
        num_topics = int(body.get("num_topics") or 5)

        auth_header = self.headers.get("Authorization", "")
        access_token = auth_header.replace("Bearer ", "").strip()
        user = verify_supabase_user(access_token)

        if not user:
            self._respond(401, {"error": "Not authenticated"})
            return

        if stage not in VALID_STAGES:
            self._respond(400, {"error": f"stage must be one of {sorted(VALID_STAGES)}"})
            return

        if not theme:
            self._respond(400, {"error": "theme is required"})
            return

        if not API_KEYS:
            self._respond(500, {"error": "No Gemini API key set on the server (GOOGLE_API_KEY / GOOGLE_API_KEY_2 / GOOGLE_API_KEY_3 / GOOGLE_API_KEY_4)"})
            return

        try:
            if stage == "plan":
                output = run_with_key_rotation("plan", theme, num_topics)
                self._respond(200, {"output": output})

            elif stage == "research":
                planner_output = body.get("planner_output") or ""
                output = run_with_key_rotation("research", theme, num_topics, planner_output)
                self._respond(200, {"output": output})

            elif stage == "condense":
                research_output = body.get("research_output") or ""
                output = run_with_key_rotation("condense", research_output)
                self._respond(200, {"output": output})

            elif stage == "links":
                research_output = body.get("research_output") or ""
                output = run_with_key_rotation("links", research_output)
                self._respond(200, {"output": output})

            elif stage == "write":
                planner_output = body.get("planner_output") or ""
                condensed_output = body.get("condensed_output") or ""
                links_output = body.get("links_output") or ""
                content = run_with_key_rotation(
                    "write", theme, num_topics, planner_output, condensed_output, links_output
                )
                self._respond(200, {"content": content})

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _respond(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())
