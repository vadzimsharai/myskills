---
name: share-oneliner
description: Use when the user needs to run a diagnostic command on another machine and return its output through a one-time upload URL.
---

# Diagnostic upload line

The sibling `share` skill provides the CLI and server. Set it up first, create a private destination with `../share/bin/share new --private`, and set `SHARE_KEY` to the last path segment of its URL. This helper also needs `jq` and GNU `date` and `find`.

1. Run `./oneliner.sh url` to create a one-time upload URL. It prints a token ID and URL. Do not open the URL yourself.
2. Give the user one bounded command line that sends its output to that URL:

   ```bash
   { echo "### host: $(hostname)"; <diagnostic-command>; } 2>&1 | bash <(curl -fsSL <upload-url>)
   ```

3. After the user runs it, use `./oneliner.sh result <token-id>` to locate the uploaded file and read it locally.

Limit logs by time or line count. Use a separate token for each machine. Treat upload URLs and diagnostic output as sensitive, and do not commit either one.
