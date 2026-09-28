"""Draw the phone the Telegram recording is shown inside.

A screen recording dropped straight onto a slide reads as a screenshot. Inside a
phone it reads as somebody's night. The frame is generated rather than sourced so
it matches the console's palette exactly and carries no vendor's industrial design.

    .venv/bin/python video/phone_frame.py            # writes video/assets/phone-*.png
"""

from __future__ import annotations

import pathlib
import sys

W, H = 1920, 1080
SCREEN_W, SCREEN_H = 414, 920  # a 720x1600 recording, scaled to fit
BEZEL = 15
RADIUS = 46
CREAM = (255, 255, 235, 255)
BODY = (26, 26, 26, 255)
ROOT = pathlib.Path(__file__).resolve().parent


def geometry(x_centre: int = W // 2) -> dict[str, int]:
    """Where the screen sits, so ffmpeg and the drawing agree on one number."""
    return {
        "screen_x": x_centre - SCREEN_W // 2,
        "screen_y": (H - SCREEN_H) // 2,
        "screen_w": SCREEN_W,
        "screen_h": SCREEN_H,
    }


def main() -> int:
    from PIL import Image, ImageDraw, ImageFilter

    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    g = geometry()
    body = (
        g["screen_x"] - BEZEL,
        g["screen_y"] - BEZEL,
        g["screen_x"] + SCREEN_W + BEZEL,
        g["screen_y"] + SCREEN_H + BEZEL,
    )

    # The background is a separate flat image so ffmpeg can loop one PNG.
    Image.new("RGBA", (W, H), CREAM).save(out / "phone-bg.png")

    # The overlay: a soft shadow, the body, and a hole where the screen is. Drawn
    # in that order so the video shows through the hole and nothing else.
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (body[0], body[1] + 14, body[2], body[3] + 18), RADIUS + 6, fill=(0, 0, 0, 70)
    )
    layer.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(26)))

    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle(body, RADIUS + 6, fill=BODY)
    # a rim, so the body reads as a device rather than a black rectangle
    draw.rounded_rectangle(body, RADIUS + 6, outline=(58, 58, 54, 255), width=2)
    # the speaker slot
    slot_w = 86
    draw.rounded_rectangle(
        (
            g["screen_x"] + SCREEN_W // 2 - slot_w // 2,
            body[1] + 5,
            g["screen_x"] + SCREEN_W // 2 + slot_w // 2,
            body[1] + 10,
        ),
        3,
        fill=(70, 70, 66, 255),
    )
    # punch the screen out
    hole = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(hole).rounded_rectangle(
        (
            g["screen_x"],
            g["screen_y"],
            g["screen_x"] + SCREEN_W,
            g["screen_y"] + SCREEN_H,
        ),
        RADIUS,
        fill=(0, 0, 0, 255),
    )
    layer.paste((0, 0, 0, 0), (0, 0), hole)
    layer.save(out / "phone-frame.png")

    print(
        f"{out / 'phone-frame.png'}  screen at {g['screen_x']},{g['screen_y']} "
        f"({SCREEN_W}x{SCREEN_H})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
