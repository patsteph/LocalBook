# LocalBook on TestFlight

## 1. Test on the phone first (about 20 minutes)

After `git pull origin dev`, `xcodegen generate`, rebuild. On the Mac: `./build.sh --rebuild`.

**Pairing (new Secure Enclave key)**
- [ ] Open the app. It asks you to pair again (expected, once). Pair with the Mac; confirm the code on the Mac.
- [ ] Settings → About shows "Pairing key: Secure Enclave".
- [ ] Mac → Sync tab: remove the old phone entry; the new phone appears. Turn Sync on for the notebooks you want.
- [ ] Sync finishes; ask a question in a notebook; it answers with sources.

**Meetings**
- [ ] Record 2 minutes, stop. The write-up screen shows steps with ticks. Name speakers (type 2 letters of an earlier name: suggestion appears). Save is top-left. Saving shows a card with a percentage.
- [ ] Ask "what did <name> say about …" in that notebook.

**Sources**
- [ ] Notebooks tab → magnifier icon on a row. Newest first, dates shown, filter chips work, A–Z in the arrows menu, tap a source to read it.

**Studio**
- [ ] Mac-made items appear on the shelf with the right dates; audio and video download on tap.
- [ ] Long-press a phone-made item: Share, Rename, Make another version, Delete each work.

**Import**
- [ ] New note → Import → an audio file, and a video from Photos. Text appears in the note.

**Lock test (the important one)**
- [ ] Ask a question. While it says "Reading N passages…", press the side button. Unlock. Did the app survive? Settings → About → Share diagnostics and send it.
- [ ] Ask again; this time lock while the answer is streaming. Same.

## 2. App Store Connect (once)

1. developer.apple.com → Identifiers: the app's bundle id `com.patsteph.localbook.ios` exists (Xcode creates it on first archive).
2. appstoreconnect.apple.com → Apps → + New App: name LocalBook, iOS, that bundle id, primary language, SKU `localbook`.
3. Xcode: scheme **LocalBook**, destination "Any iOS Device (arm64)", Product → Archive → Distribute App → App Store Connect → Upload. Version/build numbers come from project.yml; raise the build number for each upload.
4. App Store Connect → TestFlight: wait for "Processing" to finish, answer the export-compliance question (the app declares no non-exempt encryption).
5. **Internal testers** (up to 100 people on your App Store Connect team): add them under Users and Access, then TestFlight → Internal Testing. No Apple review.
6. **External testers** (anyone, by email or link): needs Apple's Beta App Review, usually about a day. Needs the notes below.

App Privacy answers: see `APP-STORE-PRIVACY.md`.

Test Information to enter in App Store Connect:
- Privacy policy URL: https://www.patrickjstephens.com/projects/localbook/privacy
- Support URL: https://www.patrickjstephens.com/projects/localbook/support
- Marketing/project page: https://www.patrickjstephens.com/projects/localbook
- Feedback and contact email: stephensuc@gmail.com

## 3. Notes for Apple's reviewers (paste into "Beta App Review Information")

> LocalBook is a companion app for the LocalBook desktop app (macOS). It searches and answers questions from the user's own notebooks, entirely on the iPhone. It needs the Mac app only to copy the notebooks once and to keep them in sync; after that it works on its own, offline.
>
> Because a reviewer will not have the Mac app, a screen recording of the full flow is attached: pairing with the Mac, syncing a notebook, asking a question, recording a meeting, and making a study guide. No account or sign-in is needed. Nothing is sent to a server: all models run on the device; the only network traffic is between the iPhone and the user's own Mac on the same Wi-Fi.
>
> Microphone: used to dictate notes and record meetings the user starts. Speech recognition: transcribes those recordings on the device. Camera: photographs pages to add to a notebook. Local network: finds and syncs with the user's Mac.

Record the demo video (about 2 minutes) with iPhone screen recording and attach it in the same section.

## 4. Known issues to tell testers

- Locking the phone while an answer is being written stops the answer safely; ask again after unlocking.
- Editing or deleting sources that came from the Mac is not on the phone yet.
- Visuals and videos made on the phone stay on the phone.
- A meeting longer than 2 hours has not been tested.
