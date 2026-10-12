# Approved production policy, 12 October 2026

116 approved chapters, 408 segments. Iapetus (audition 09), exact wording with singular musical תו pointed תָּו including prefixes. Reuse approved 00-introduction.mp3 in every chapter; preserve original numbered title, track and filename. No API calls for the introduction. Ten unresolved chapters excluded: 10, 16, 17, 18, 20, 36, 40, 69, 83, 106. Generated audio awaits listening review.

One serial request at a time. Only GEMINI_API_KEY_1, _2, _3; immediately rotate on HTTP 429. After all usable keys fail, sleep 10, 20, 40 seconds and double to 1800 seconds maximum. Every HTTP 200 resets waiting to 10 seconds. Log every non-200 response with secret redaction and detailed progress. Stop initiating requests after 19800 seconds, allow the current request to finish and save. Persist each segment to independent audiobook-v2-data branch with text/configuration/checksum identity; never replace old production audio.

Hourly GitHub Actions at minute 17, with duplicate guard and producer concurrency (never cancel an active producer). Scheduled times can be delayed by GitHub. Workflow timeout 350 minutes. Manual first run authorized by user. No chapter uploads to Drive and no release publication in this batch.
