# 03. LocalBook for Mac: design profile (one page)

*2026-11-13, for Patrick's review (Phase 0 gate). Extends the iOS profile (`/mnt/project-files/ios-design/LocalBook-iOS-design-profile.md` merged with `ios/LocalBookLab/DESIGN.md`) to the Mac. Same goal: Speed, Simplicity, Functionality, Beauty; calm. Built on the seven traits of award-winning Mac apps in the research report, section 3.2.*

## The idea

One window, one idea: **ask a notebook**. The sidebar is the library, the middle is the conversation, everything else is one step away through the toolbar, a menu or a shortcut. The app opens in under a second with the model warming in the background and the composer live. It looks and behaves like a Mac app Apple made: system sidebar, toolbar, menus, Settings window, light and dark and the user's accent color, SF Symbols, system text styles, VoiceOver and full keyboard control. SwiftUI by default; AppKit (`NSOutlineView`, `NSTextView`, `NSTableView`) wherever selection, focus, rich text or density feel wrong, on sight, not after a debate.

## Windows

| Window | What it is | Shortcut |
|---|---|---|
| Main (any number) | Sidebar + content. Each window remembers its notebook. Tabs via the system tab bar | ⌘N new window (system), ⌘T new tab |
| Settings | Tabs: General, Sync, Models, Library, Agents, Security, Diagnostics | ⌘, |
| Source viewer | PDF, web page or note, opened from a citation or a source row; Quick Look for a peek | Space (peek), ⌘↓ (open) |
| Studio item | A briefing, quiz, flashcards, audio overview or visual in its own window when detached | ⌘⇧D detach |
| Meeting recorder | A small always-on-top panel while recording | ⌘⇧R |
| Canvas, Constellation | Their own windows (Phase 5) | from the Studio menu |

## Sidebar (left, 200 to 320 pt)

Notebooks in the desktop app's order with its sections and colors (the rule the phone follows), then **Library**: Studio, Meetings, Notes. Right-click gives New Note, Sync, Rename, Keep local / Sync everywhere, Delete. Drag files or URLs onto a notebook to add sources. A sync dot per notebook; "Mac not reachable, showing local copy" as one quiet line, never a modal. Search field at the top filters notebooks; ⌘⇧F finds across the selected notebook's sources.

## Content (middle)

**Ask** is the first screen. Notebook name as the window title, readiness as the subtitle ("Getting ready", "Ready", "Not paired"). Conversation runs down the middle at a 60 to 70 character measure; the composer is pinned at the bottom with Send that becomes Stop while streaming (⌘.). Real phases while waiting ("Searching Sales notebook", "Reading 6 passages", "Writing"), sources appear before the answer, text streams without reflowing earlier paragraphs, Reduce Motion drops the fade. Citations are numbered inline anchors; clicking opens the passage in a popover, double-click opens the source viewer. Under a finished answer: Copy, Share, Read aloud, answer length. The right edge holds an **inspector** (⌥⌘I) with the notebook's sources, selectable for scoping.

**Sources**, **Studio**, **Meetings** and **Notes** are the same panes the phone has, laid out for a wide window: lists on the left of the pane, the item on the right, with multi-select, drag out to Finder, Quick Look and Share. Studio's "making it" animation and celebratory quiz results come across as they are.

## Toolbar

Left to right: sidebar toggle (system), notebook picker (when the sidebar is hidden), **Sync** with inline progress, **Add Sources** (menu: Files, Web page, Folder, Paste), **Studio** (menu of the five kinds), **Record**, search, inspector toggle. Customisable the system way.

## Menu bar map

| Menu | Items (shortcut) |
|---|---|
| LocalBook | About, Settings… (⌘,), Services, Hide, Quit |
| File | New Notebook… (⌘N), New Note (⇧⌘N), Add Sources… (⌘O), Add Web Page… (⇧⌘L), Watch Folder…, Close (⌘W), Export Notebook… (⇧⌘E), Print… |
| Edit | System: Undo, Redo, Cut, Copy, Paste, Select All, Find (⌘F in a source or note), Spelling, Dictation, Emoji |
| View | Show/Hide Sidebar (⌃⌘S), Show Sources (⌥⌘1), Show Studio (⌥⌘2), Show Meetings (⌥⌘3), Show Inspector (⌥⌘I), Enter Full Screen |
| Notebook | Ask (⌘L), Stop Answer (⌘.), Sync Now (⌘R), Pair with a Mac…, Read Answer Aloud (⌥⌘R), Keep Local / Sync Everywhere |
| Studio | Briefing (⌃⌘1), Quiz (⌃⌘2), Flashcards (⌃⌘3), Audio Overview (⌃⌘4), Visual (⌃⌘5), Video Explainer, Image…, Canvas, Constellation |
| Record | Start Meeting Recording (⇧⌘R), Voice Note (⇧⌘V) |
| Window | System: Minimize, Zoom, tabs, Bring All to Front |
| Help | LocalBook Help, Privacy |

Every item exists in the app from Phase 0, disabled until its feature lands. No action is reachable only by mouse.

## Keyboard map beyond the menus

↑/↓ move in lists; Return opens; Space peeks (Quick Look); ⌘1 to ⌘9 jump to the first nine notebooks; Esc stops an answer or closes a popover; Tab and Shift-Tab move focus through sidebar, conversation, composer, inspector; ⌘↩ sends from the composer when Return is set to insert a newline (General setting).

## Platform conventions we adopt

Drag and drop in (files, URLs, text onto a notebook) and out (sources, Studio items, answers as text or Markdown). Services ("Ask LocalBook about selection"). Share sheet on every item. Quick Look for sources and Studio items. Core Spotlight indexing of notebooks and sources. App Intents and a Shortcuts gallery (Ask, Start Recording, Make a Briefing). Focus-aware notifications for finished Studio items and briefings. Open at login as a menu bar presence is **not** adopted: the app is a document tool, not a utility. Updates via Sparkle or the App Store (Phase 4 decides).

## Look

System appearance plus the existing accent themes as a Settings choice; no custom chrome, no glass on content. System text styles; the conversation at body size with quiet headings. One motion idea (short springs, meaningful only), one tone of voice (plain, encouraging, never alarming). Empty states say the one thing to do next. Dynamic Type (Mac text size) and VoiceOver tested on the Ask screen before Phase 2 closes. The app icon is the phone's chosen "quiet fan" (C3c) in the macOS shape.

## What it is not

Not a port of the 25-panel web UI. Panels become places in this map (a sidebar item, a Settings tab, a window or an inspector), never a new top-level screen. If a feature does not fit the map, the map changes here first.
