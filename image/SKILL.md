---
name: image
description: Use when the user asks to generate a raster image through the Replicate API and save it as a local asset.
---

# Generate an image

The `gen.sh` script is bundled here. Run it from the target project's directory and pass the script's path explicitly. It reads `REPLICATE_API_TOKEN` from the environment or from an ignored `.env` file next to the script.

```bash
bash <skill-dir>/gen.sh "<prompt>" [--model <owner/name>] [--ar <W:H>] [--size <WxH>] [--output <path>] [--seed <n>] [--format png|webp|jpg] [--style <style>]
```

The default model is `black-forest-labs/flux-1.1-pro`. `--ar` sets the aspect ratio for supported models; `--size` sets explicit dimensions for models that require them. `--output` chooses the destination file. Without it, the script writes to `assets/generated/` under the current directory.

Use the bundled script for authentication, polling, and download. Report the saved file path. If the user asks to use the image in a project, follow that project's existing asset conventions.

If the token is missing, ask the user to configure it locally. Do not print or commit the token. If the API returns an error or unexpected output, report the error and the model used.
