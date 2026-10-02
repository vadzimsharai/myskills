---
name: share
description: Use when the user asks to publish local files or a generated page, create an expiring link, or manage a share served by the bundled host.
---

# Share files and pages

The CLI is `bin/share`; the Go server and Docker Compose file are in `server/`. Run commands from this skill directory. The first setup is:

```bash
./setup.sh
./bin/share up --build
```

The server listens on localhost by default. To publish links for other people, configure HTTPS routing to the server and set `SHARE_BASE_URL` to that public URL. Set `SHARE_ROOT` to a persistent local directory if the default is unsuitable. The generated `server/.env` contains a fresh signing secret and must stay private.

```bash
./bin/share status
./bin/share new --title "<title>" --desc "<description>" <path>
./bin/share add <key> <path>
./bin/share link <key> [path] --ttl 24h
./bin/share url <key>
./bin/share path <key>
./bin/share rotate <key>
./bin/share rm <key>
```

Use `share new` for a file or folder. For a generated page, put `index.md` or `index.html` in a temporary folder and share it. Confirm that the returned URL works before handing it to the user. Use an expiring link for short access and rotate a key if a link must be invalidated. Delete a share only when requested.

`./bin/share feedback on <key>` enables answers for the `discuss` skill; `feedback show` reads them. `./bin/share token <key> --once --ttl 30m` creates a one-time upload URL for `share-oneliner`. Treat that URL as a write credential.

Check files for credentials and private data before publishing. Anyone with a working read URL may be able to see its content.
