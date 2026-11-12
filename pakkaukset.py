"""
Sample packs in a library whose packs are not all directly under the library folder.

A library made by one person over the years has folders above the packs: by vendor
("UNDRGRND Sounds/UNDRGRND Sounds - Grime 2.0 - Wav/…"), by genre ("TECH HOUSE/<pack>/…"), or just
"lots of stuff/SAMPLES/<pack>/…". Fetch reads three things from the pack level, so it has to find it:
  * the pack's name gives the genre (genret.py), and the user confirms genres per pack
  * names of packs and of the folders above them are titles, not descriptions of the content: an
    instrument word in "Dark Bass 2" or "Techno Machine" says nothing about a file below it (soittimet.py)
  * accuracy is measured with whole packs held out (testit/tarkkuus.py)

The pack is found from the folder tree alone (paths in the index; no disk access):
a folder is a CONTAINER of packs when most of what is in it sits in subfolders that have a folder
structure of their own and are not named like the parts of a pack ("Drum Loops", "One Shots", "VEC3 Kicks").
Otherwise it is a PACK. Measured on a large library with packs 1–4 levels deep.

Standard library + soittimet's word lists.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

import polut
import soittimet

# Words that name a part of a pack rather than a product ("Drum Loops", "One Shots", "Construction Kits & MIDI",
# "WAV", "Bonus"). Instrument and tag words come from soittimet.
_PARTS = set("""loop loops lp lps one shot shots oneshot oneshots hit hits single singles sample samples sound sounds
drum drums kit kits construction midi preset presets stem stems wet dry bonus music musical melodic melodics melody
melodies tonal atonal instrument instruments inst misc other others various extras extra tools elements layers full
groove grooves beat beats rhythm rhythms percussive hi and fx effects efx sfx backgrounds soundscapes vinyl crackle raw
processed clean dirty wav wave aiff aif rex rx2 apple acid ableton kontakt battery serum massive sylenth exs24 nnxt
halion maschine individual combo combos toploops rolls builds buildups transitions multi audio files file folder demo
demos artwork info documentation docs main basic basics all mixed mix mixes parts part sequence sequences seq seqs
tops top chord chords songstarter songstarters starters ideas inspiration mp3 mp3s previews preview racks project
projects""".split())
# Words that say nothing about what is inside: a folder named only with these ("SAMPLES", "Audio Files", "Misc")
# can be a part of a pack or a pile of packs; its content decides.
_GENERIC = set("""sample samples sound sounds audio files file folder wav wave aiff aif mp3 mp3s misc other others various
extras extra bonus demo demos main all mixed and""".split())
CATEGORY_WORDS = set(soittimet.KW) | set(soittimet.TW) | set(soittimet.KIT_ABBR) | _PARTS
_NOISE = re.compile(r"\d+(bpm|st|nd|rd|th)?|bpm|v\d+|vol\d*|pt\d*|[a-g](#|b)?(m|min|maj|minor|major)?|.")


def _words(name: str) -> list[str]:
    """A folder name as lower-case words without numbers, tempos, keys and single letters."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    out = []
    for t in re.sub(r"[^A-Za-z0-9#]+", " ", s).lower().split():
        if _NOISE.fullmatch(t):
            continue
        if t.endswith("z") and t[:-1] + "s" in CATEGORY_WORDS:      # "Kickz", "Loopz"
            t = t[:-1] + "s"
        out.append(t)
    return out


# what a part of a pack is called for: an instrument, a style tag or a kind of sample (not "Kit": "9th Wonder Kit")
_HEADS = (set(soittimet.KW) | set(soittimet.TW) | set("""loop loops lps one shot shots oneshot oneshots hit hits single
singles stem stems midi preset presets fx sfx effects efx groove grooves beat beats fills rolls""".split())) - _GENERIC


def category_named(names: list[str], by_head: bool = False) -> dict[str, bool]:
    """For folders next to each other: is each named like a part of a pack? A prefix most of them share
    ("VEC3 Kicks", "VEC3 Loops 140 BPM") is a product code and is ignored.
    by_head: a short name that ends in what it holds also counts ("Long Shouts", "Rasta Phrases", "Sung
    Phrases") – good enough to tell a pack from a pile of packs, too loose to tell a title from a part
    ("Dark Bass 2" is a product)."""
    W = {n: _words(n) for n in names}
    for _ in range(4):
        first = Counter(w[0] for w in W.values() if w)
        if not first:
            break
        tok, c = first.most_common(1)[0]
        if c < 2 or c < 0.6 * len(W) or tok in CATEGORY_WORDS:
            break
        W = {n: (w[1:] if w and w[0] == tok else w) for n, w in W.items()}
    out = {}
    for n, w in W.items():
        ok = [t in CATEGORY_WORDS for t in w]
        for k in range(len(w) - 1):                                  # "ad libs" = adlibs, "one shots" = oneshots
            if w[k] + w[k + 1] in CATEGORY_WORDS:
                ok[k] = ok[k + 1] = True
        out[n] = all(ok) and not all(t in _GENERIC for t in w)       # "SAMPLES" alone names no part
        if by_head and not out[n] and 0 < len(w) <= 3:
            out[n] = w[-1] in _HEADS or (len(w) > 1 and w[-2] + w[-1] in _HEADS)
    return out


class Tree:
    """Folder statistics of the indexed paths: files directly in a folder, files below it, subfolders."""

    def __init__(self, paths, roots_per_path):
        self.direct, self.total, self.kids = Counter(), Counter(), defaultdict(set)
        for p, r in zip(paths, roots_per_path):
            cur = r
            self.total[cur] += 1
            start = len(r) + 1
            while True:
                k = p.find("/", start)
                if k < 0:
                    break
                nxt = p[:k]
                self.kids[cur].add(nxt)
                cur = nxt
                self.total[cur] += 1
                start = k + 1
            self.direct[cur] += 1


def _is_container(t: Tree, f: str) -> bool:
    kids = t.kids.get(f)
    if not kids:
        return False
    tot = t.total[f]
    if t.direct[f] > max(5, 0.1 * tot):                 # samples lie in it directly: a pack
        return False
    below = tot - t.direct[f]
    cat = category_named([k.rsplit("/", 1)[-1] for k in kids], by_head=True)
    parts = [k for k in kids if cat[k.rsplit("/", 1)[-1]]]
    if 2 * len(parts) >= len(kids) or sum(t.total[k] for k in parts) >= 0.5 * below:
        return False                                    # its folders are the parts of a pack (most of them, or
                                                        # most of the files: one big "Vocals" next to small drum folders)
    # folders with a structure of their own (or a single folder inside them and nothing else) = packs
    shaped = sum(t.total[k] for k in kids
                 if len(t.kids.get(k, ())) >= 2 or (len(t.kids.get(k, ())) == 1 and not t.direct[k]))
    return shaped >= 0.5 * below


def find(paths, roots=None) -> tuple[list[str], list[int]]:
    """-> (pack folder, number of title folders) for every path.
    The pack folder is a "/"-path ("<root>/TECH HOUSE/Toolroom Tech House Essentials"); a file directly in a
    library root is its own pack, as before. Title folders = the leading folders under the library root
    that are names of products or collections (the containers and the pack): 1 in a library with packs
    directly under it. A pack-level folder named like a part ("ESW SAMPLES/Drums") is not a title."""
    rp = polut.roots_per_path(paths, roots)
    t = Tree(paths, rp)
    container: dict[str, bool] = {}
    part_named: dict[str, bool] = {}

    def visit(f: str, named_part: bool) -> None:
        part_named[f] = named_part
        container[f] = (not named_part) and _is_container(t, f)
        if container[f]:
            kids = sorted(t.kids[f])
            cat = category_named([k.rsplit("/", 1)[-1] for k in kids])
            for k in kids:
                visit(k, cat[k.rsplit("/", 1)[-1]])

    for r in set(rp):
        for k in t.kids.get(r, ()):
            visit(k, False)                             # directly under the library: judged by its content only
    packs, titles = [], []
    for p, r in zip(paths, rp):
        start = len(r) + 1
        k = p.find("/", start)
        if k < 0:                                       # a file directly in the library folder
            packs.append(p)
            titles.append(0)
            continue
        n = 0
        while True:
            f = p[:k]
            n += 1
            nxt = p.find("/", k + 1)
            if not container.get(f) or nxt < 0:
                break
            k = nxt
        packs.append(f)
        n = n - 1 if part_named.get(f) and not container.get(f) else n
        # the pack's only folder, named like a product again ("X Kit/X Kit/…" as unzipped): a title too
        g = f
        while nxt >= 0:
            kids = t.kids.get(g, ())
            if len(kids) != 1 or t.direct[g] > max(1, 0.02 * t.total[g]):
                break
            g = p[:nxt]
            if g not in kids or category_named([g.rsplit("/", 1)[-1]], by_head=True)[g.rsplit("/", 1)[-1]]:
                break
            n += 1
            nxt = p.find("/", nxt + 1)
        titles.append(n)
    return packs, titles


def pack_key(pack: str, root: str) -> str:
    """The pack as the user's genre settings name it: its path from the library folder
    ("TECH HOUSE/Toolroom Tech House Essentials"; just the folder name when directly under the library)."""
    return pack[len(root):].lstrip("/")
