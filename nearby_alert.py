#!/usr/bin/env python3
"""
Horizon -> Discord: bagitau bila ada player dekat dengan kau.

Guna `horizon-ctl` (agent control) untuk baca state game, lepas tu hantar
alert ke Discord webhook. Tak perlu install apa-apa package (stdlib sahaja).

Setup:
  1. Discord: Server Settings -> Integrations -> Webhooks -> New Webhook -> Copy URL
  2. Jalankan game dengan control socket ON (`--control`), atau guna
     `horizon-ctl launch --world X --wait`.
  3. export DISCORD_WEBHOOK="https://discord.com/api/webhooks/...."
     (jangan share URL ni, sesiapa ada URL boleh post kat channel kau)
  4. python3 nearby_alert.py --radius 64

Kalau output JSON `entities` lain sikit dari yang aku jangka, jalankan
dengan --debug untuk tengok raw output, lepas tu adjust fungsi extract_*().
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request


def run_ctl(ctl, *args, timeout=10):
    """Jalankan horizon-ctl, return JSON yang di-parse (atau None kalau gagal)."""
    try:
        out = subprocess.run(
            [ctl, *args], capture_output=True, text=True, timeout=timeout
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"[!] horizon-ctl error: {e}", file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"[!] horizon-ctl {' '.join(args)} -> exit {out.returncode}: "
              f"{out.stdout.strip() or out.stderr.strip()}", file=sys.stderr)
        return None
    line = out.stdout.strip().splitlines()
    if not line:
        return None
    try:
        return json.loads(line[-1])
    except json.JSONDecodeError:
        print(f"[!] bukan JSON: {line[-1][:200]}", file=sys.stderr)
        return None


def get_xyz(obj):
    """Cuba ambil (x, y, z) dari pelbagai bentuk JSON yang mungkin."""
    if not isinstance(obj, dict):
        return None
    for key in ("pos", "position", "location"):
        v = obj.get(key)
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            return float(v[0]), float(v[1]), float(v[2])
        if isinstance(v, dict) and all(k in v for k in "xyz"):
            return float(v["x"]), float(v["y"]), float(v["z"])
    if all(k in obj for k in "xyz"):
        return float(obj["x"]), float(obj["y"]), float(obj["z"])
    return None


def extract_entities(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("entities", "mobs", "results"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def is_player(ent):
    kind = str(ent.get("type") or ent.get("kind") or ent.get("id") or "").lower()
    return "player" in kind


def player_name(ent):
    for key in ("name", "username", "player", "display_name"):
        if ent.get(key):
            return str(ent[key])
    return f"player#{ent.get('id', '?')}"


def send_discord(webhook, content, embed=None):
    payload = {"content": content}
    if embed:
        payload["embeds"] = [embed]
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "horizon-nearby-alert"},
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except urllib.error.HTTPError as e:
        print(f"[!] Discord HTTP {e.code}: {e.read()[:200]!r}", file=sys.stderr)
    except Exception as e:
        print(f"[!] Discord error: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ctl", default=os.environ.get("HORIZON_CTL", "horizon-ctl"),
                    help="path ke horizon-ctl")
    ap.add_argument("--webhook", default=os.environ.get("DISCORD_WEBHOOK"),
                    help="Discord webhook URL (atau set DISCORD_WEBHOOK)")
    ap.add_argument("--radius", type=float, default=64, help="radius dalam block (default 64)")
    ap.add_argument("--interval", type=float, default=2.0, help="saat antara setiap check")
    ap.add_argument("--cooldown", type=float, default=120,
                    help="saat sebelum player sama di-alert semula (default 120)")
    ap.add_argument("--me", default=os.environ.get("MC_NAME"),
                    help="username kau, supaya diri sendiri tak masuk alert")
    ap.add_argument("--notify-leave", action="store_true", help="alert bila player pergi jauh")
    ap.add_argument("--debug", action="store_true", help="print raw JSON dari horizon-ctl")
    args = ap.parse_args()

    if not args.webhook and not args.debug:
        sys.exit("Set DISCORD_WEBHOOK atau guna --webhook (atau --debug untuk test tanpa Discord).")

    print(f"[*] Pantau player dalam {args.radius} block. Ctrl+C untuk berhenti.")
    last_alert = {}   # nama -> masa alert terakhir
    present = set()   # player yang sedang dekat

    while True:
        try:
            me = run_ctl(args.ctl, "state")
            ents = run_ctl(args.ctl, "entities", str(int(args.radius)), "player")
            if args.debug:
                print("state    :", json.dumps(me)[:500])
                print("entities :", json.dumps(ents)[:1000])
            if me is None or ents is None:
                time.sleep(args.interval)
                continue

            my_pos = get_xyz(me) or get_xyz(me.get("player", {}) if isinstance(me, dict) else {})
            dimension = me.get("dimension", "?") if isinstance(me, dict) else "?"

            now = time.time()
            current = {}
            for ent in extract_entities(ents):
                if not isinstance(ent, dict) or not is_player(ent):
                    continue
                name = player_name(ent)
                if args.me and name.lower() == args.me.lower():
                    continue
                pos = get_xyz(ent)
                dist = math.dist(my_pos, pos) if (my_pos and pos) else None
                if dist is not None and dist > args.radius:
                    continue
                current[name] = (pos, dist)

            for name, (pos, dist) in current.items():
                is_new = name not in present
                cooled = now - last_alert.get(name, 0) >= args.cooldown
                if is_new and cooled:
                    last_alert[name] = now
                    d = f"{dist:.0f} block" if dist is not None else "?"
                    coords = (f"{pos[0]:.0f}, {pos[1]:.0f}, {pos[2]:.0f}" if pos else "?")
                    print(f"[ALERT] {name} dekat ({d})")
                    if args.webhook:
                        send_discord(
                            args.webhook,
                            f"⚠️ **{name}** dekat dengan kau ({d})",
                            {
                                "title": "Player dikesan",
                                "color": 0xE74C3C,
                                "fields": [
                                    {"name": "Player", "value": name, "inline": True},
                                    {"name": "Jarak", "value": d, "inline": True},
                                    {"name": "Dimension", "value": str(dimension), "inline": True},
                                    {"name": "Koordinat dia", "value": coords, "inline": False},
                                ],
                            },
                        )

            if args.notify_leave and args.webhook:
                for name in present - set(current):
                    send_discord(args.webhook, f"✅ **{name}** dah jauh dari kau.")

            present = set(current)
        except KeyboardInterrupt:
            print("\n[*] Berhenti.")
            break
        except Exception as e:  # jangan biar script mati sebab satu error
            print(f"[!] error: {e}", file=sys.stderr)

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
