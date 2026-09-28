#!/usr/bin/env bash
# Put the phone recording inside a phone, and cut it to the beats that matter.
#
#   video/telegram_cut.sh <long-take.mp4> <resolved-take.mp4> <out-dir>
#
# The long take opens with a minute of waiting — which is honest, and is also not
# what anybody needs to watch. It starts where the page lands.
set -euo pipefail
cd "$(dirname "$0")/.."

A="${1:?usage: telegram_cut.sh <long.mp4> <resolved.mp4> [outdir]}"
B="${2:?}"
OUT="${3:-$HOME/beacon-video/telegram}"
mkdir -p "$OUT"

FRAME=video/assets/phone-frame.png
BG=video/assets/phone-bg.png
X=753; Y=80; SW=414; SH=920

# where the usable material is, read off the recording
A_IN=66        # the page lands
A_OUT=216      # "Approved. Applying and verifying now"
B_IN=0         # the resolved message
B_OUT=9        # before the browser takes over

frame_it () {   # <in> <ss> <to> <out>
    ffmpeg -hide_banner -loglevel error -y \
        -loop 1 -i "$BG" \
        -ss "$2" -to "$3" -i "$1" \
        -i "$FRAME" \
        -filter_complex "[1:v]scale=$SW:$SH,setsar=1[v]; \
                         [0:v][v]overlay=$X:$Y:shortest=1[bg]; \
                         [bg][2:v]overlay=0:0,format=yuv420p,fps=30[out]" \
        -map "[out]" -map 1:a? -c:v libx264 -crf 18 -preset medium \
        -c:a aac -b:a 192k -shortest "$4"
}

echo "==> framing the conversation (${A_IN}s–${A_OUT}s)"
frame_it "$A" "$A_IN" "$A_OUT" "$OUT/part1.mp4"
echo "==> framing the resolution (${B_IN}s–${B_OUT}s)"
frame_it "$B" "$B_IN" "$B_OUT" "$OUT/part2.mp4"

echo "==> joining"
printf "file '%s'\nfile '%s'\n" "$OUT/part1.mp4" "$OUT/part2.mp4" > "$OUT/join.txt"
ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$OUT/join.txt" \
    -c copy "$OUT/telegram-full.mp4"

ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/telegram-full.mp4" \
    | xargs printf "done  %s  %.0f s\n" "$OUT/telegram-full.mp4"
