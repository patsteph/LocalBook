# App Store privacy and compliance answers: LocalBook (iPhone)

Written 2026-10-26 from what the code does. Re-check if the app starts talking to any server other than your own Mac.

**Data collection ("App Privacy" in App Store Connect): Data Not Collected.**
Your notebooks, questions, notes, recordings and Studio items stay on the phone and sync only with your own Mac over your local network. No analytics, ads or accounts. Model files download from a public model host; nothing about you is sent.

**Tracking:** No.

**Privacy manifest** (`Privacy/LocalBook/PrivacyInfo.xcprivacy`): no tracking, no collected data, and the two "required reason" APIs the app itself uses:
- UserDefaults, reason CA92.1 (the app's own settings).
- File timestamps, reason C617.1 (dates of files inside the app's own folder).
If Apple's upload check emails about another API, it comes from a library; tell me the category and I'll add it.

**Export compliance:** `ITSAppUsesNonExemptEncryption = NO`. The app only uses encryption built into iOS: TLS to your Mac and ECDSA signing for pairing. No custom or non-standard cryptography.

**Permission texts (shown by iOS, already in the app):** local network, microphone, speech recognition, camera. Photos and Files pickers need no permission.

**You supply in App Store Connect:**
1. A privacy policy URL (required even for apps that collect nothing; a one-page "nothing leaves your devices" statement is enough).
2. A support email or URL.
3. Review notes: the app needs a paired Mac running LocalBook to be useful. Offer Apple a short screen recording of pairing and asking a question, or a test Mac setup, since reviewers will not have your Mac.
