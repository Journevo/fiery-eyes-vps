# YouTube Module — Cookie & API Setup

## Why cookies are needed

YouTube blocks transcript/caption downloads from datacenter IPs (bot detection).
Providing browser cookies lets yt-dlp and youtube-transcript-api authenticate as
a logged-in user, bypassing most blocks.

## Exporting cookies

1. Install the **"Get cookies.txt LOCALLY"** Chrome extension
   ([Chrome Web Store link](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc))
2. Go to [youtube.com](https://www.youtube.com) and make sure you are logged in
3. Click the extension icon and choose **Export** (Netscape format)
4. Save the file as `cookies.txt`

## Configuring the cookie file

Copy the exported file to the server and set the env var in `.env`:

```
YOUTUBE_COOKIES_FILE=/opt/fiery-eyes/cookies.txt
```

If `YOUTUBE_COOKIES_FILE` is not set, the default path `<project_root>/cookies.txt`
is used.

## Verifying it works

```bash
# Test yt-dlp caption download with cookies
./venv/bin/yt-dlp --cookies cookies.txt --write-auto-sub --sub-lang en \
  --skip-download -o /tmp/test "https://www.youtube.com/watch?v=VIDEO_ID"
```

If a `.vtt` file appears in `/tmp/`, cookies are working.

## Fallback chain

When transcripts are unavailable, the pipeline degrades gracefully:

1. **youtube-transcript-api** with cookies + proxy
2. **yt-dlp** caption download with cookies
3. **YouTube Data API** metadata (title + description) with lightweight Claude
   analysis (marked as "partial", relevance capped at 50)
4. Skip video (no crash)

## YouTube Data API key (optional)

For the metadata fallback (step 3), set `YOUTUBE_API_KEY` in `.env`:

```
YOUTUBE_API_KEY=your_key_here
```

Get a free key at https://console.cloud.google.com/apis/credentials
(enable "YouTube Data API v3").

## Cookie refresh

Cookies expire periodically. If transcripts start failing again, re-export
from your browser and replace the file on the server.
