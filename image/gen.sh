#!/usr/bin/env bash
# /image gen.sh — Replicate-backed image generator for Claude Code skill.
#
# Usage:
#   gen.sh "<prompt>" [--model <id>] [--size <WxH>] [--ar <W:H>]
#                     [--output <path>] [--seed <n>] [--format png|webp|jpg]
#                     [--style <flux|illustration|hand-drawn|...>]
#
# Defaults:
#   model   = black-forest-labs/flux-1.1-pro
#   ar      = 1:1
#   format  = png
#   output  = ./assets/generated/<slug>-<ts>.png
#
# Outputs the saved file path on stdout (last line). On failure: non-zero exit + error to stderr.

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
[[ -f "$SKILL_DIR/.env" ]] && set -a && source "$SKILL_DIR/.env" && set +a

if [[ -z "${REPLICATE_API_TOKEN:-}" ]]; then
  echo "ERR: REPLICATE_API_TOKEN not set (looked at $SKILL_DIR/.env)" >&2
  exit 2
fi

# ---- arg parsing ----
PROMPT=""
MODEL="black-forest-labs/flux-1.1-pro"
ASPECT="1:1"
SIZE=""
OUTPUT=""
SEED=""
FORMAT="png"
STYLE=""
IMAGE_INPUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)   MODEL="$2"; shift 2 ;;
    --size)    SIZE="$2"; shift 2 ;;
    --ar)      ASPECT="$2"; shift 2 ;;
    --output|-o) OUTPUT="$2"; shift 2 ;;
    --seed)    SEED="$2"; shift 2 ;;
    --format)  FORMAT="$2"; shift 2 ;;
    --style)   STYLE="$2"; shift 2 ;;
    --image)   IMAGE_INPUT="$2"; shift 2 ;;
    --) shift; PROMPT="$*"; break ;;
    -*) echo "ERR: unknown flag: $1" >&2; exit 2 ;;
    *)  PROMPT+="${PROMPT:+ }$1"; shift ;;
  esac
done

to_data_url() {
  local file="$1" mime
  [[ -f "$file" ]] || { echo "ERR: --image file not found: $file" >&2; exit 2; }
  case "$file" in
    *.png) mime="image/png" ;;
    *.jpg|*.jpeg) mime="image/jpeg" ;;
    *.webp) mime="image/webp" ;;
    *) mime="application/octet-stream" ;;
  esac
  printf 'data:%s;base64,%s' "$mime" "$(base64 -w0 "$file")"
}

if [[ -z "$PROMPT" ]]; then
  echo "ERR: empty prompt" >&2
  exit 2
fi

# ---- output path ----
if [[ -z "$OUTPUT" ]]; then
  TS=$(date +%s)
  SLUG=$(echo "$PROMPT" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/-\+/-/g; s/^-//; s/-$//' | cut -c1-40)
  OUTPUT="assets/generated/${SLUG:-image}-${TS}.${FORMAT}"
fi
mkdir -p "$(dirname "$OUTPUT")"

# ---- build per-model JSON input ----
case "$MODEL" in
  black-forest-labs/flux-1.1-pro|black-forest-labs/flux-1.1-pro-ultra|black-forest-labs/flux-pro|black-forest-labs/flux-dev|black-forest-labs/flux-schnell)
    INPUT_JSON=$(jq -nc \
      --arg prompt "$PROMPT" \
      --arg ar "$ASPECT" \
      --arg fmt "$FORMAT" \
      --arg seed "$SEED" \
      '{prompt:$prompt, aspect_ratio:$ar, output_format:$fmt} + (if $seed != "" then {seed: ($seed|tonumber)} else {} end)')
    ;;
  recraft-ai/recraft-v3)
    # Recraft uses size + style. Convert ar to size if size not given.
    if [[ -z "$SIZE" ]]; then
      case "$ASPECT" in
        1:1)  SIZE="1024x1024" ;;
        16:9) SIZE="1820x1024" ;;
        9:16) SIZE="1024x1820" ;;
        4:3)  SIZE="1365x1024" ;;
        3:4)  SIZE="1024x1365" ;;
        *)    SIZE="1024x1024" ;;
      esac
    fi
    RECRAFT_STYLE="${STYLE:-digital_illustration}"
    INPUT_JSON=$(jq -nc \
      --arg prompt "$PROMPT" \
      --arg size "$SIZE" \
      --arg style "$RECRAFT_STYLE" \
      '{prompt:$prompt, size:$size, style:$style}')
    ;;
  ideogram-ai/ideogram-v2|ideogram-ai/ideogram-v2-turbo)
    INPUT_JSON=$(jq -nc \
      --arg prompt "$PROMPT" \
      --arg ar "$ASPECT" \
      '{prompt:$prompt, aspect_ratio:$ar}')
    ;;
  stability-ai/stable-diffusion-3.5-large|stability-ai/stable-diffusion-3.5-large-turbo|stability-ai/stable-diffusion-3.5-medium)
    INPUT_JSON=$(jq -nc \
      --arg prompt "$PROMPT" \
      --arg ar "$ASPECT" \
      --arg fmt "$FORMAT" \
      '{prompt:$prompt, aspect_ratio:$ar, output_format:$fmt}')
    ;;
  *)
    # Generic: pass prompt + best-guess fields, let Replicate validate.
    INPUT_JSON=$(jq -nc --arg prompt "$PROMPT" --arg ar "$ASPECT" '{prompt:$prompt, aspect_ratio:$ar}')
    ;;
esac

PAYLOAD=$(jq -nc --argjson input "$INPUT_JSON" '{input:$input}')

# ---- POST to model-prediction endpoint with synchronous wait ----
URL="https://api.replicate.com/v1/models/${MODEL}/predictions"
HTTP_BODY=$(curl -sS -X POST "$URL" \
  -H "Authorization: Bearer ${REPLICATE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Prefer: wait=60" \
  --data-raw "$PAYLOAD")

# Detect HTTP-level errors (Replicate returns {title, detail, status: <http-code>} on 4xx/5xx).
# A normal prediction response has .id and .urls; an error has .title.
TITLE=$(echo "$HTTP_BODY" | jq -r '.title // empty')
if [[ -n "$TITLE" ]]; then
  DETAIL=$(echo "$HTTP_BODY" | jq -r '.detail // empty')
  HTTP_STATUS=$(echo "$HTTP_BODY" | jq -r '.status // empty')
  echo "ERR: Replicate API error [$HTTP_STATUS] $TITLE${DETAIL:+: $DETAIL}" >&2
  case "$TITLE" in
    "Insufficient credit"|"Payment Required") echo "      → Top up at https://replicate.com/account/billing" >&2 ;;
    "Unauthenticated"|"Authentication failed") echo "      → Check REPLICATE_API_TOKEN in $SKILL_DIR/.env" >&2 ;;
  esac
  exit 1
fi

STATUS=$(echo "$HTTP_BODY" | jq -r '.status // "unknown"')

# ---- if not yet finished, poll ----
if [[ "$STATUS" != "succeeded" && "$STATUS" != "failed" ]]; then
  GET_URL=$(echo "$HTTP_BODY" | jq -r '.urls.get // empty')
  if [[ -z "$GET_URL" ]]; then
    echo "ERR: no get-url in response: $HTTP_BODY" >&2
    exit 1
  fi
  for _ in $(seq 1 60); do
    sleep 2
    HTTP_BODY=$(curl -sS -H "Authorization: Bearer ${REPLICATE_API_TOKEN}" "$GET_URL")
    STATUS=$(echo "$HTTP_BODY" | jq -r '.status')
    [[ "$STATUS" == "succeeded" || "$STATUS" == "failed" || "$STATUS" == "canceled" ]] && break
  done
fi

if [[ "$STATUS" != "succeeded" ]]; then
  ERR=$(echo "$HTTP_BODY" | jq -r '.error // "unknown error"')
  echo "ERR: prediction $STATUS: $ERR" >&2
  exit 1
fi

# ---- extract output URL (string OR array of strings depending on model) ----
OUTPUT_URL=$(echo "$HTTP_BODY" | jq -r 'if (.output|type) == "array" then .output[0] else .output end')

if [[ -z "$OUTPUT_URL" || "$OUTPUT_URL" == "null" ]]; then
  echo "ERR: no output url in response: $HTTP_BODY" >&2
  exit 1
fi

# ---- download ----
curl -sSL -o "$OUTPUT" "$OUTPUT_URL"

# ---- summary ----
SIZE_BYTES=$(stat -c '%s' "$OUTPUT" 2>/dev/null || stat -f '%z' "$OUTPUT")
echo "model: $MODEL" >&2
echo "prompt: $PROMPT" >&2
echo "size: $SIZE_BYTES bytes" >&2
echo "$OUTPUT"
