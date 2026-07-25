# Article Topic Generator (Vercel + Supabase)

Same 5-stage Gemini pipeline as the original Streamlit app, now:
- deployed as a Next.js app on Vercel (instead of Streamlit)
- protected by Supabase email/password login
- history saved to a Supabase Postgres table instead of `st.session_state`
  (so it survives refreshes and regenerations)

## What changed vs. what didn't

- **Unchanged:** the pipeline's 5 stages and their exact role/goal/backstory/
  task text (Topic Planner, Topic Researcher, Summary Generator, Link
  Collector, Article Prompt Writer), the Gemini 3.5 Flash model choice and
  temperature, the 5-10 random topic count, the visual theme/colors, the
  download-as-.md behavior, and the "expand/collapse + delete" history UI.
- **Changed (because Streamlit can't run on Vercel):** the UI is now a
  Next.js page instead of a Streamlit script, and generation runs as a
  Vercel Python serverless function (`api/generate.py`).
- **Changed (forced by Vercel's platform limits):** the pipeline no longer
  runs through the `crewai` library. CrewAI's own dependency tree
  (chromadb, onnxruntime, langchain, a kubernetes client, etc.) is ~800MB
  by itself regardless of which features you use, and Vercel hard-caps
  serverless functions at 500MB total. `api/generate.py` now calls Gemini
  directly for each of the 5 stages in sequence, feeding each stage's
  output into the next — same handoff behavior as CrewAI's sequential
  process, same prompts, just without the framework. This brought the
  function down to ~194MB installed.

## One-time setup

1. **Create a Supabase project** at supabase.com (free tier is fine).
2. In the SQL editor, run `supabase/schema.sql` once. This creates the
   `history` table and its row-level security policies (each user only
   ever sees their own rows).
3. In Supabase → Authentication → Providers, email/password is enabled
   by default — nothing else to do there. Optionally turn off "Confirm
   email" under Authentication → Settings if you don't want the
   email-verification step during signup.
4. Copy `.env.example` to `.env.local` and fill in:
   - `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` (Supabase
     → Project Settings → API)
   - `GOOGLE_API_KEY` (Gemini key). Optionally also set `GOOGLE_API_KEY_2`,
     `GOOGLE_API_KEY_3`, and `GOOGLE_API_KEY_4` to rotate across up to 4
     keys — note free-tier quota is per Google Cloud **project**, so these
     only help if each key comes from a separate project.

## Local dev

```bash
npm install
npm run dev
```

## Deploy to Vercel

```bash
npm i -g vercel
vercel
```

Add the same three env vars in Vercel → Project → Settings →
Environment Variables (for Production, and Preview if you use it), then
redeploy.

Note: the pipeline runs 5 sequential Gemini calls, which can take longer
than Vercel's default serverless timeout. `vercel.json` sets
`maxDuration: 60` for `api/generate.py` — Vercel's Hobby plan caps
functions at 60s, so if generation is timing out for you on a busier
theme, a Pro plan lets you raise this further.
