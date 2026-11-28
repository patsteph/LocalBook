# Flights 1–6 + visuals close-out: one sitting (2026-11-27)

About 70 minutes at the Mac, plus two unattended runs. Everything below is in the Swift Mac app unless it says "old app".

**Before:** `git pull origin dev && git log --oneline -1` (expect the latest dev commit, at or after `bdd7b87`), rebuild the
Mac app in Xcode. Test files: `testdata/flights/` (this folder) and `testdata/phase3/`. Old app quit unless a step says otherwise
(both apps listen on port 8000). For section G, a second Terminal: `cd testdata/flights && python3 -m http.server 8765`, left running.
For section H, a third Terminal: `python3 scripts/flights_mailserver.py testdata/flights/mail` (a throwaway IMAP + SMTP server on this Mac), left running.

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

## F. Flight 4: Canvas and Constellation (8 minutes; Harbor Insights has questions from D by now)

20. Ask Harbor Insights two more questions, one it cannot answer ("What is the Denver roastery's square footage?"). Sidebar › **Canvas**
    (⌥⌘5) › **Build the map**. **Pass:** a chip per question with the answer's first lines and a source count; a chip per source an
    answer used (none for a file nobody asked about); the quiz or finding from D; cards gathering related questions with a model-given
    title; the square-footage question wears the orange badge and is under **Gaps**; thin lines from sources to questions; dotted
    suggestions between related chips. The status line reads "N things on the map · M cards · …".
21. Drag a chip, drag a card (its chips move with it), **Build the map** again: both stay put. **Tidy up**: re-arranged. Shift-click two
    chips › **Connect these two**: a solid line. Select the 01-company-history chip and ask "When did the Austin move happen?" in the
    bar at the bottom. **Pass:** the answer chip lands beside it joined by a line, and the answer used only that source.
22. **Review**: Show the answer, grade one Good; the chip loses its review badge. Insights › Contradictions already exists, so the map
    shows a **red line** between 01 and 02; click it: the two claims in the inspector. **Send back:** any of 20–22 that did not happen.
23. Sidebar › **Constellation** (⌥⌘6) (the knowledge graph from step 3 feeds it; otherwise **Build the constellation**). **Pass:** coloured
    stars in 3 to 5 clusters, the biggest star of each at its centre with its name; drag to orbit, scroll to zoom; click Rosa Alvarez:
    "Connected to" lists Harbor Lights Coffee, Portland, Austin … and "Named in" lists files; **Source** › 02-press-clipping keeps only
    its stars; the legend's cluster counts add up to the stars line. **Send back:** a screenshot if it looks wrong, one word if fine.

## G. Flight 5: Curator and Collector (12 minutes; the http.server from "Before" is running)

24. Sidebar › **Collector** (⌥⌘8) with Harbor Lights selected › **Setup**: name "Harbor watch", subject "Harbor Lights Coffee", intent
    "shipping policy and regional expansion", focus areas `shipping`, `Denver`, excluded topics `recipes`, approval **Mixed**, collect
    **Daily**. › **Sources**: add `http://127.0.0.1:8765/pages/about.html` (**Pass:** it lands under Feeds as `…/feeds/harbor-news.xml`,
    not under Watched pages), add `http://127.0.0.1:8765/pages/shipping.html` (under Watched pages). Keywords: `Harbor Lights Coffee`.
25. **Collect now**. **Pass:** History shows one run "N looked at · a added · q queued · s skipped" with the recipes item among the skipped;
    the Denver roastery and shipping policy items are either added as sources (Sources pane of Harbor Lights) or in the **Queue** with a
    percent and reasons like "Relevance … Trusted source … fresh"; the keyword search adds whatever Hacker News / Reddit / Wikipedia return
    (offline: a problem line, nothing else breaks). **Approve** one queued item: it appears under Sources. **Dismiss** another with a reason:
    it shows under "Decided lately". Edit `pages/shipping.html` in a text editor (change the price), **Collect now** again: one changed-page item.
    Without an edit, a third run collects nothing new. **Send back:** the run lines and anything that did not match.
26. **Research**: "Harbor Lights Coffee Denver roastery", Web search, **Start**. **Pass:** the job shows progress then "done" with a count
    and results in the Queue with percents. Then **Deep dive** on "cold brew coffee shelf life". **Pass:** a few summarized results with a
    quality percent each (needs the internet; offline, a plain problem line).
27. Sidebar › **Curator** (⌥⌘7) › profile button: name "Marlowe", voice **Executive brief**, leave out one notebook, Save. **Brief** (or ⌥⌘B).
    **Pass:** a piece in that voice naming today's added sources (the Collector's), the questions asked and the Studio items, per notebook,
    the left-out notebook absent. **Week**: a seven-day look-back. **Patterns**: at least one connection Harbor Lights ↔ Harbor Insights
    (the shipping credit) with both passages and a percent. **Devil's advocate** against "Harbor Lights should open more roasteries":
    an argument that cites notebooks. **Digest** of Harbor Insights. **Save as note** on the brief: under Notes. **Useful** / **Dismiss** stick.
28. Terminal, with the smoke key from step 4: `curl -s -H "Authorization: Bearer <key>" -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"events_since","arguments":{}}}' http://127.0.0.1:8000/mcp`
    **Pass:** JSON events of kinds source_added, question_asked, collected, approved … Same with `"name":"todays_digest"`: the brief's text.
    `/brief`, `/research <topic>` and `/collect` in the Ask field open the right pane and run.

## H. Flight 6: the Correspondent (10 minutes; the mail server from "Before" is running; Harbor Lights and Harbor Insights exist)

29. Sidebar › **Correspondent** (⌥⌘9) › **Inboxes** › **Add an inbox** › **IMAP account**: email `you@example.com`, IMAP host `127.0.0.1`,
    port `1143`, **TLS off**, any app password; SMTP host `127.0.0.1`, port `1025`, **TLS off**. Both toggles on, "Forwards with no match go to"
    the waiting list. **Test**. **Pass:** "Signed in; 5 unread messages waiting". **Add**. (The server accepts any user and password;
    the real thing wants TLS on and port 993 / 465, and refuses a plain connection to anything but this Mac.)
30. **Check mail now** (or ⌥⇧⌘M, or `/mail`). The status line walks through Opening, Looking, Reading, Sorting, Adding. **History**: one run
    "5 looked at · … · 1 forward added · … queued". **Pass**, message by message (the server's Terminal prints each move):
    `02-forward` (Fwd: shipping policy `#harbor-insights`) lands in **Harbor Insights** as a source titled "Shipping policy: free over $40 from
    December", and a confirmation reply appears as `testdata/flights/mail/outbox/<stamp>.eml` reading "Got it — ingested into Harbor Insights
    as source …"; `01-newsletter` (Denver roastery) is either a new source of Harbor Lights or under **Waiting** with a percent and "best match:
    Harbor Lights" (the 75 percent line decides); `05-newsletter-unrelated` (Knit Notes) is under **Waiting** with a low percent, never a source;
    `03-personal` (Rosa's lunch) is **moved to LocalBook/Personal** on the server and nowhere in the app; `04-receipt` is marked read and left.
    The server's Terminal shows `03-personal.eml -> LocalBook/Personal`, `flags of 04-receipt.eml -> ['\\Seen']`, and the forward deleted
    (`-> [Gmail]/Trash` is only for Gmail; here it is flagged Deleted and expunged). **Check mail now** again: "0 looked at", nothing twice.
31. **Waiting**: on the Knit Notes card pick Harbor Lights in the Notebook picker › **Approve**: it becomes a source there, and the card goes.
    **Dismiss** the other (if any). The approval teaches the Correspondent: that sender now leans 25 percent toward Harbor Lights next time.
32. **Journal**: the preview "This week's letter" names today's sources (the forward, the Collector's) and the questions asked; **Send now**.
    **Pass:** a second `.eml` in `mail/outbox/` with the letter as text and HTML. (The timer sends it on its own once a week, mornings only,
    when something happened; empty weeks send nothing.)
33. **Mail on this Mac** (optional, 3 minutes, only if Mail.app has an account on this Mac): **Add an inbox** › **Mail on this Mac** ›
    **Choose Mail folder…** › `~/Library/Mail` › if the orange line asks for it, **Open Full Disk Access settings**, tick LocalBook, come back
    › pick an inbox › **Test**: "Mail's index opens; the inbox is at message N" › **Add**, then **Check now** on that row. The first run looks
    only at mail that arrives after you added it (nothing old is read), so forward yourself one message with `#harbor-lights` in the subject
    and check again; macOS asks once to let LocalBook control Mail. **Send back:** the Test line and whether the forward landed.
    Delete that inbox afterwards (trash icon) if you do not want it polling every 8 hours.

**Send back:** the History run line from 30, which of 01 / 05 landed where, and any of 29–33 that did not happen. Ctrl-C the mail server
and delete `testdata/flights/mail/outbox/` when done (it is git-ignored).

## What to send back, in one message

The step numbers that did not pass with what you saw (one line each), the C4 rate and three differences, the C6 Reveal line,
the Collector run lines (25), the Correspondent run line (30). Everything else is taken as passed.
