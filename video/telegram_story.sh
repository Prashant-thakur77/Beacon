#!/usr/bin/env bash
# The Telegram take, cut to its seven beats, with the phone on the left and the
# caption on the right. Timestamps are into telegram-full.mp4.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-$HOME/beacon-video/telegram}"
FULL="$OUT/telegram-full.mp4"
CAPS="$HOME/beacon-video/caps"
TMP="$OUT/tmp"; mkdir -p "$TMP"

# beat: in out caption — chosen from what actually happens on screen
BEATS=(
  "2   9   01"    # the page lands
  "56  63  02"    # heard at 98%
  "69  77  03"    # the CloudTrail change
  "117 125 04"    # one change, read back
  "127 140 05"    # 75%, then 74% — refused
  "142 150 06"    # typed, approved, verifying
  "150 157 07"    # resolved, with the quote
)

i=0; list="$TMP/list.txt"; : > "$list"
for b in "${BEATS[@]}"; do
  read -r IN OUTT CAP <<< "$b"
  i=$((i+1)); part="$TMP/beat$(printf %02d $i).mp4"
  # the phone slides left, the caption sits in the space it leaves
  ffmpeg -hide_banner -loglevel error -y \
    -ss "$IN" -to "$OUTT" -i "$FULL" \
    -i "$CAPS/cap$CAP.png" \
    -filter_complex "[0:v]crop=560:1080:680:0,pad=1920:1080:120:0:color=0xFFFFEB[p]; \
                     [p][1:v]overlay=820:0,format=yuv420p[out]" \
    -map "[out]" -map 0:a? -c:v libx264 -crf 18 -preset medium \
    -c:a aac -b:a 192k "$part"
  echo "file '$part'" >> "$list"
  printf "  beat %d  %ss-%ss  cap%s\n" "$i" "$IN" "$OUTT" "$CAP"
done

ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$list" -c copy "$OUT/telegram-story.mp4"
ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/telegram-story.mp4" \
  | xargs printf "done  %s  %.0f s\n" "$OUT/telegram-story.mp4"
