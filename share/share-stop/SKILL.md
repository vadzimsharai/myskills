---
name: share-stop
description: Use when the user explicitly asks to stop the bundled share service or make its links unavailable.
---

# Stop the share service

Run the sibling `share` skill's CLI:

```bash
../share/bin/share status
../share/bin/share down
../share/bin/share status
```

Confirm the service stopped. Report any failure and whether links remain accessible. Do not remove shared files unless the user asks for deletion.
