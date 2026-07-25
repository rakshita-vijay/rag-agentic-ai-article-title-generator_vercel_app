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
import requests
import google.generativeai as genai

SUPABASE_URL = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")


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

    def __init__(self):
        GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
        if not GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY environment variable not set.")
        genai.configure(api_key=GOOGLE_API_KEY)
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

        if not os.environ.get("GOOGLE_API_KEY"):
            self._respond(500, {"error": "GOOGLE_API_KEY not set on the server"})
            return

        try:
            generator = ArticleTopicGenerator()

            if stage == "plan":
                output = generator.plan(theme, num_topics)
                self._respond(200, {"output": output})

            elif stage == "research":
                planner_output = body.get("planner_output") or ""
                output = generator.research(theme, num_topics, planner_output)
                self._respond(200, {"output": output})

            elif stage == "condense":
                research_output = body.get("research_output") or ""
                output = generator.condense(research_output)
                self._respond(200, {"output": output})

            elif stage == "links":
                research_output = body.get("research_output") or ""
                output = generator.links(research_output)
                self._respond(200, {"output": output})

            elif stage == "write":
                planner_output = body.get("planner_output") or ""
                condensed_output = body.get("condensed_output") or ""
                links_output = body.get("links_output") or ""
                content = generator.write(
                    theme, num_topics, planner_output, condensed_output, links_output
                )
                self._respond(200, {"content": content})

        except Exception as e:
            self._respond(500, {"error": str(e)})

    def _respond(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())
