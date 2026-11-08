"""Concept figure for the 3D risk map (illustrative numbers only)."""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# Castle-like palette
INK = "#171717"; GRAY = "#737373"; LINE = "#E5E5E5"; BG = "#FFFFFF"
TEAL = "#00544F"; AMBER = "#B7791F"; SLATE = "#64748B"; RISK = "#7C4632"; BLUE = "#3A5C9A"
TIER_COLORS = {"A": TEAL, "B": AMBER, "C": SLATE, "D": RISK}

for f in font_manager.findSystemFonts():
    if "Inter" in f:
        font_manager.fontManager.addfont(f)
plt.rcParams["font.family"] = "Inter"
plt.rcParams["font.size"] = 10

# Portfolio exposure (QQQ-like): % P&L per typical move on (growth, inflation, risk appetite)
b = np.array([2.0, -1.5, 2.5])
u = b / np.linalg.norm(b)

# Illustrative events: position = how the event pushes (growth, inflation, risk appetite)
# (name, position, odds, tier, label offset)
events = [
    ("Tariffs on China 25%+", (-1.0, 1.0, -1.0), 0.40, "A", (-0.9, 0.0, 0.1)),
    ("Hormuz oil shock", (-0.7, 1.6, -1.3), 0.15, "C", (0.2, 0.9, -0.15)),
    ("US recession", (-2.5, -1.0, -2.4), 0.30, "A", (0.0, 0.0, 0.5)),
    ("Fed cuts in March", (1.0, 0.5, 1.2), 0.65, "A", (-0.6, 0.0, 0.55)),
    ("Ukraine ceasefire", (0.5, -1.3, 0.8), 0.25, "B", (0.0, -0.3, 0.45)),
    ("Fiscal stimulus", (1.2, 1.6, 0.0), 0.35, "C", (0.5, 0.5, -0.45)),
    ("Chip export ban", (-0.3, 0.4, -1.6), 0.20, "D", (0.0, 0.0, -0.55)),
]

fig = plt.figure(figsize=(12.5, 7.2), dpi=200, facecolor=BG)
ax = fig.add_axes([0.30, 0.0, 0.70, 0.90], projection="3d", facecolor=BG)
ax.view_init(elev=18, azim=-62)
lim = 2.6
ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim); ax.set_zlim(-lim, lim)
for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
    axis.set_pane_color((0.97, 0.97, 0.97, 1.0))
    axis._axinfo["grid"]["color"] = LINE
    axis._axinfo["grid"]["linewidth"] = 0.6
ax.set_xlabel("Growth", color=GRAY, labelpad=6)
ax.set_ylabel("Inflation", color=GRAY, labelpad=6)
ax.set_zlabel("Risk appetite", color=GRAY, labelpad=6)
ax.tick_params(colors=GRAY, labelsize=7)

# Origin = portfolio today
ax.scatter([0], [0], [0], s=40, color=INK, depthshade=False)

# Portfolio arrow and its backward extension (the loss side)
L = 2.6
ax.quiver(0, 0, 0, *(u * L), color=INK, linewidth=2.2, arrow_length_ratio=0.08)
ax.plot(*np.vstack([np.zeros(3), -u * L]).T, color=INK, linewidth=1, linestyle=(0, (2, 3)), alpha=0.6)
ax.text(*(u * (L + 0.15) + np.array([0.9, -0.6, -0.2])), "Your portfolio (QQQ)\ngains this way", color=INK, fontsize=9, ha="left")
ax.text(*(-u * (L + 0.35)), "losses this way", color=GRAY, fontsize=8, ha="center")

rows = []
for name, pos, q, tier, off in events:
    p = np.array(pos)
    impact = float(b @ p)  # % move on the portfolio if the full jump happens
    proj = (p @ u) * u     # foot of the projection on the arrow's line
    c = TIER_COLORS[tier]
    ax.plot(*np.vstack([p, proj]).T, color=c, linewidth=0.8, linestyle=(0, (1, 2)), alpha=0.9)
    ax.scatter(*p, s=60 + 900 * q, color=c, alpha=0.85, edgecolors="white", linewidths=0.8, depthshade=False)
    ax.text(p[0] + off[0], p[1] + off[1], p[2] + off[2], f"{name} · {q:.0%}", fontsize=7.6, color=INK, ha="center")
    rows.append((name, q, impact, tier))

# Legend for tiers
from matplotlib.lines import Line2D
handles = [Line2D([0], [0], marker="o", color="w", markerfacecolor=TIER_COLORS[t], markersize=8, label=l)
           for t, l in [("A", "A · Confirmed"), ("B", "B · Contested"), ("C", "C · Provisional"), ("D", "D · Unpriced")]]
leg = fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.015, 0.86), frameon=False, fontsize=8.5, title="Confidence tier", title_fontsize=8.5, alignment="left")
rows.sort(key=lambda r: -(r[1] * (1 - r[1]) * r[2] ** 2))
y = 0.50
fig.text(0.015, y + 0.05, "Ranked by swing", fontsize=9, color=INK)
fig.text(0.015, y + 0.025, "full jump on QQQ  ·  if it happens / if not", fontsize=7.6, color=GRAY)
for name, q, imp, tier in rows:
    hap = (1 - q) * imp; nothap = -q * imp
    fmt = lambda x: ("+" if x > 0.05 else ("−" if x < -0.05 else "±")) + f"{abs(x):.1f}%"
    fig.text(0.015, y, f"{name}", fontsize=7.6, color=TIER_COLORS[tier], family="DejaVu Sans Mono")
    fig.text(0.155, y, f"{fmt(imp):>7}   {fmt(hap):>7} / {fmt(nothap):>7}", fontsize=7.6, color=INK, family="DejaVu Sans Mono")
    y -= 0.03
fig.text(0.5, 0.955, "The Red Keep · 3D risk map (concept, illustrative numbers)", ha="center", fontsize=12, color=INK)
fig.text(0.5, 0.925, "Dot = event (size = odds, color = tier). Impact on you = how far a dot sits along your arrow, not its distance from the origin.",
         ha="center", fontsize=8.2, color=GRAY)
out = __import__("sys").argv[1]
fig.savefig(out, dpi=200, facecolor=BG)
print("saved", out)
