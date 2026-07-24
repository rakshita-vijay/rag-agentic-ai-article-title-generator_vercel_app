# Article Topic Generator (Vercel + Supabase)

Same CrewAI/Gemini multi-agent pipeline as the original Streamlit app, now:
- deployed as a Next.js app on Vercel (instead of Streamlit)
- protected by Supabase email/password login
- history saved to a Supabase Postgres table instead of `st.session_state`
  (so it survives refreshes and regenerations)

## What changed vs. what didn't

- **Unchanged:** the entire CrewAI agent/task pipeline (planner, researcher,
  condenser, collector, writer), the Gemini 2.0 Flash model choice, the
  5-10 random topic count, the visual theme/colors, the download-as-.md
  behavior, and the "expand/collapse + delete" history UI.
- **Changed (because Streamlit can't run on Vercel):** the UI is now a
  Next.js page instead of a Streamlit script, and the generation function
  runs as a Vercel Python serverless function (`api/generate.py`) instead
  of being called inline in `app.py`. The agent/task code inside it is a
  straight copy of `streamlit_version/generator.py`, just without the
  Streamlit-only progress callback.

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
   - `GOOGLE_API_KEY` (same Gemini key as before)

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
Environment Variables, then redeploy.

Note: the CrewAI pipeline runs 5 sequential agent calls, which can take
longer than Vercel's default serverless timeout. `vercel.json` sets
`maxDuration: 60` for `api/generate.py` — Vercel's Hobby plan caps
functions at 60s, so if generation is timing out for you on a busier
theme, a Pro plan lets you raise this further.
