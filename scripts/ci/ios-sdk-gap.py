#!/usr/bin/env python3
"""Separate "the runner's SDK is older than the app's" from real compile errors in an xcodebuild log.

The iPhone app targets iOS 27 and uses AVFAudio API that only the iOS 27 SDK has (session activate/deactivate,
didBecomeInactive / resumption notifications, AVAudioEngine.connectNode, AVAudioPlayerNode.playAudio). GitHub's
macos-26 runners ship Xcode 26.x (iOS 26.5 SDK) as of 2026-11-13, so those lines cannot compile there, while
everything else — above all the shared package and its use from the app — can and must.

Exit 0 when every error in the log is one of those known SDK gaps (and say so); exit 1 otherwise, listing the rest.
Once the runner has Xcode 27 the build simply succeeds and this script is never reached.
"""
import re, sys

KNOWN_FILES = ("ios/LocalBookLab/Sources/AppShell/MeetingRecorder.swift",
               "ios/LocalBookLab/Sources/AppShell/ReadAloud.swift")
KNOWN_MESSAGES = (
    "'activate(options:)' is unavailable in iOS",
    "has no member 'didBecomeInactiveNotification'",
    "has no member 'resumptionRecommendationNotification'",
    "has no member 'resumptionContextKey'",
    "'ResumptionContext' is not a member type",
    "has no member 'connectNode'",
    "has no member 'playAudio'",
    "has no member 'deactivate'",
    "cannot infer contextual base in reference to member 'notifyOthersOnDeactivation'",
)

log = open(sys.argv[1], errors="replace").read()
errors = sorted(set(re.findall(r"(/[^\s:]+\.swift:\d+:\d+: error: .*?)(?=\\n|\n|$)", log)))
known, other = [], []
for e in errors:
    path = e.split(":", 1)[0]
    if path.endswith(KNOWN_FILES) and any(m in e for m in KNOWN_MESSAGES):
        known.append(e)
    else:
        other.append(e)

if not errors:
    print("No Swift errors in the log, yet xcodebuild failed; see the log above."); sys.exit(1)
print(f"{len(known)} error(s) are the known iOS 27 SDK gap on this runner's Xcode:")
for e in known: print("  (sdk gap)", e.split("/LocalBook/LocalBook/")[-1])
if other:
    print(f"\n{len(other)} other error(s) — these are real:")
    for e in other: print("  ", e.split("/LocalBook/LocalBook/")[-1])
    sys.exit(1)
print("\nNothing else failed: the package extraction builds; only the iOS 27 SDK calls are missing on this Xcode.")
