# Flights 1–3 + visuals close-out: one sitting (2026-11-27)

About 40 minutes at the Mac, plus two unattended runs. Everything below is in the Swift Mac app unless it says "old app".

**Before:** `git pull origin dev && git log --oneline -1` (expect the latest dev commit, at or after `29088ef`), rebuild the
Mac app in Xcode. Test files: `testdata/flights/` (this folder) and `testdata/phase3/`. Old app quit unless a step says otherwise
(both apps listen on port 8000).

## A. Unattended first (10 minutes of clicks, then walk away)

1. **Visuals close-out, old app's half** (old app running): `scripts/old_app_closeout.sh <AI Research notebook id>`.
   Writes the C4 fixture, then draws the old app's 15. Quit the old app when it finishes.
2. **Visuals close-out, Swift half**: Settings › Library › Studio visuals: FLUX.2 Klein 4B, Best: two passes, critic ON.
   Settings › Diagnostics › Visual golden set › AI Research › Draw the 15 (start after step 1 finishes). Critic OFF afterwards.
3. **Insights notebook**: New notebook "Harbor Insights", drop the five files from `testdata/flights/insights-notebook/` on it.
   Then Insights (sidebar) › pick Harbor Insights › **Timeline**, then **Knowledge graph**, then **Contradictions**, then **Topics**,
   one after another (each is a minute or two; the pane shows progress). Meanwhile do section B.

## B. Flight 1: agents reach the app (8 minutes)

4. Settings › **Agents**: status says listening on 127.0.0.1:8000. **Issue a key** named "smoke" (all notebooks ticked); copy it.
   **Send back:** nothing unless the status is not "listening".
5. Terminal: `scripts/flights_smoke.sh <key>`. **Pass:** health JSON; initialize returns a session id; tools/list names
   `list_notebooks, list_sources, search_notebook, search_notebooks, ask_notebook, …`; list_notebooks shows your notebooks;
   search_notebook returns passages; `/v1/models` lists the answer model; the completion answers; the bad key gets 401.
   **Send back:** the first line that is not that.
6. Claude Code (optional, 1 minute): paste the command from the key's row (`claude mcp add …`), ask Claude "list my LocalBook
   notebooks". Settings › Agents › **Calls** shows the call. The Jocasta/Claude Desktop `.mcpb` bridge needs no change: same port, same tools.
7. In Ask, type `@curator which notebooks mention shipping credits?` **Pass:** an answer with chips that read "notebook · file"
   from more than one notebook (Harbor Lights and Harbor Insights both have the 5 percent credit). Then ask a plain question in
   Harbor Insights: it must cite only that notebook.
8. Settings › Agents › **Memory**: Add "Patrick prefers short answers", then in Terminal re-run the smoke script and check nothing broke;
   delete the memory. Browser extension (optional): the old extension against the Swift app: its popup must show your notebooks
   and a capture of any page must land in the notebook you choose (same `/browser/*` routes, same pinned id).

## C. Flight 2: own your data (8 minutes)

9. Settings › **Security** › Choose… a folder on the Desktop. **Create a recovery phrase**, Copy, paste into a note somewhere,
   "I wrote it down". **Back up now.** **Pass:** `LocalBook-<Mac>-<stamp>.lbbackup` in the folder, status line with the file count.
10. Rename a notebook and add one note. In Settings › Security › In the folder › **Restore…** on that archive (opens with this Mac's key,
    no phrase asked) › Restore at next launch › **Quit now**. Reopen. **Pass:** the rename and the note are gone; the Security tab's
    first line says "Restored on …"; a `Restore-replaced-<stamp>` folder sits beside the library (Settings › Library › Show in Finder).
11. **Forget** the phrase; **Use a phrase I already have…** with the copied words (accepted), then once more with one word changed
    (refused with the checksum line).
12. **Open a backup…** › `testdata/flights/old-app-sample.lbbackup`. It asks for the phrase; paste the one in `old-app-sample.phrase.txt`.
    **Pass:** "2 files … made … on golden-device" and the line that it is from the old app and cannot be restored here (no Restore button).
13. Studio › **Audio overview** on Harbor Lights: pick two American voices (Nova and Adam), 2 segments. **Video**: narrator Lily.
    **Pass:** those voices on the result. Settings › General: turn **Keep the tone even across models** off, ask a question, on, ask again;
    both answers stay inside the notebook.

## D. Flight 3: Insights (10 minutes, the four from step 3 should be done)

14. **Timeline**: events in order 2014 → Mar 2015 → Jun 2017 → 2019 → Q4 2021 → Aug 2022 → Feb 2023 → … → Q2 2024, each with its file;
    undated ones (if any) last. **Knowledge graph**: Rosa Alvarez the biggest dot; click it: Harbor Lights Coffee, Portland, Austin,
    Denver, Daniel Okafor, Priya Raman among the connections, with a relation phrase on the strongest.
15. **Contradictions**: the counts line, then the pairs. **Pass:** 63 vs 71 employees and 2014 vs 2015 founding are listed (high or
    medium); if 25 vs 40 percent growth is listed it is low or explained as different measures. Dismiss one, open another insight, come
    back: it stays dismissed. **Topics**: 3 to 4 named groups (history, shipping, region, wellness) with keyword chips; make Topics again:
    the same groups.
16. **Comparison**: 01-company-history vs 02-press-clipping, focus "numbers". **Pass:** employee count and founding year under
    "They differ on". **Perspectives**: topic "company growth". **Pass:** Agreed (Austin move, subscription) and Contested (headcount) filled,
    one card per source that speaks to it.
17. Ask Harbor Insights "Who runs the Mountain region?"; under the answer, **Keep as finding**. Insights › **Findings**: it is there;
    star it, add tags "people, mountain". **Writing assistant** (⌥⌘W): paste the shipping policy's first paragraph, Expand + Email,
    notebook as reference, Write, **Save as note**. **Pass:** the note under Notes.

## E. Visuals close-out judging (5 minutes, when step 2 has finished)

18. **C4**: Settings › Diagnostics › Ingestion diff › Open the fixture's folder… › `~/LocalBook/testdata/phase3/notebook` › Compare now ›
    Open report. **Send back:** the identical rate and the first three differences.
19. **C6**: Visual golden set › Show in Finder, ⌥-drag the `visual-golden-<number>` folder to the Desktop, then
    `python3 scripts/visual_golden_blind.py ~/Desktop/visual-golden-<number> ~/Documents/visual-golden`, open `blind.html`,
    pick Left / Tie / Right for the 15, Reveal. Glance at the Swift index.html: 13–15 have a painted backdrop with crisp text; critic lines sane.
    **Send back:** the Reveal summary line (bar 12 of 15), one word on the backdrops, any critic line that looks wrong.

## What to send back, in one message

The step numbers that did not pass with what you saw (one line each), the C4 rate and three differences, the C6 Reveal line.
Everything else is taken as passed.
