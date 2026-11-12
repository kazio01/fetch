"""
Genres for Fetch – Splice's genre list. Genre is a property of the pack
(measured: per-sample genre from audio is 20–38 % correct, at pack level 69 %).

A sample's genres:
  1) a "By Genre/Latin"-style subfolder (most precise), otherwise
  2) the pack's genres: confirmed by the user (genret.json) > from the pack name > from the folders above
     the pack ("TECH HOUSE/<pack>") > the analysis suggestion (the pack's average sound closest to named
     packs), which is shown only as a suggestion in the genre list until the user confirms it
     (measured 69 % – too uncertain to use directly).

Filter genre:<genre> or genre:<group> (e.g. genre:tech-house, genre:house-techno).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import polut

CFG = Path(os.environ.get("SAMPLEHAKU_GENRES", Path.home() / ".config/samplehaku/genret.json"))

# (group, name, [(genre, name, regular expression or None = by name only)])
_G = [
    ("hiphop-rnb", "Hip Hop / R&B", [
        ("hip-hop", "Hip Hop", r"hip.?hop"), ("trap", "Trap", r"\btrap\b"), ("rnb", "R&B", r"\br\s*&\s*b\b|\brnb\b"),
        ("pluggnb", "Pluggnb", r"pluggnb"), ("rage", "Rage", None), ("drill", "Drill", r"(?<!uk )\bdrill\b"),
        ("uk-drill", "UK Drill", r"uk.?drill"), ("lo-fi-hip-hop", "Lo-Fi Hip Hop", r"lo.?fi.?hip|bedroom beats"),
        ("boom-bap", "Boom Bap", r"boom.?bap"), ("brazilian-funk", "Brazilian Funk", r"baile|funk carioca|brazilian funk"),
        ("soul", "Soul", r"(?<!neo )(?<!future )\bsoul\b"), ("neo-soul", "Neo Soul", r"neo.?soul"),
        ("future-soul", "Future Soul", r"future.?soul"), ("reggaeton", "Reggaeton", r"reggaeton"),
        ("dancehall", "Dancehall", r"dancehall"), ("jersey-club", "Jersey Club", r"jersey.?club"),
        ("moombahton", "Moombahton", r"moombah"), ("future-bass", "Future Bass", r"future.?bass"),
        ("glitch-hop", "Glitch Hop", r"glitch.?hop")]),
    ("house-techno", "House / Techno", [
        ("techno", "Techno", r"(?<!melodic )(?<!hard )(?<!dub )(?<!minimal )(?<!deep )\btechno\b|\btekno\b"),
        ("house", "House", r"(?<!tech )(?<!deep )(?<!afro )(?<!bass )(?<!acid )(?<!french )(?<!future )"
                           r"(?<!melodic )(?<!progressive )(?<!tropical )(?<!fidget )(?<!room )\bhouse\b"),
        ("tech-house", "Tech House", r"tech.?house"), ("deep-house", "Deep House", r"deep.?house"),
        ("nu-disco", "Nu Disco", r"nu.?disco"), ("afro-house", "Afro House", r"afro.?house"),
        ("amapiano", "Amapiano", r"amapiano"), ("disco", "Disco", r"(?<!nu )\bdisco\b"),
        ("electro", "Electro", r"\belectro\b"), ("minimal-techno", "Minimal Techno", r"minimal"),
        ("dub-techno", "Dub Techno", r"dub.?techno"), ("hard-techno", "Hard Techno", r"hard.?techno|harder side"),
        ("uk-garage", "UK Garage", r"uk.?garage|\bukg\b|(?<!speed )\bgarage\b"),
        ("speed-garage", "Speed Garage", r"speed.?garage"), ("rave", "Rave", r"\brave\b"),
        ("melodic-techno", "Melodic Techno", r"melodic.?(house and deep )?techno"),
        ("french-house", "French House", r"french.?house|filter.?house"), ("acid-house", "Acid House", r"acid.?house"),
        ("bass-house", "Bass House", r"bass.?house"), ("progressive-house", "Progressive House", r"progressive"),
        ("melodic-house", "Melodic House", r"melodic.?house"), ("big-room-house", "Big Room House", r"big.?room"),
        ("hardstyle", "Hardstyle", r"hardstyle")]),
    ("pop-edm", "Pop / EDM", [
        ("pop", "Pop", r"(?<!k-)(?<!k )(?<!synth )(?<!indie )(?<!hyper)(?<!bedroom )(?<!dream )\bpop\b"),
        ("edm", "EDM", r"\bedm\b"), ("indie-pop", "Indie Pop", r"indie.?pop"),
        ("afropop-and-afrobeats", "Afropop & Afrobeats", r"afro.?pop|afrobeats"), ("hyperpop", "Hyperpop", r"hyperpop"),
        ("synth-pop", "Synth-Pop", r"synth.?pop"), ("k-pop", "K-Pop", r"k.?pop"), ("bedroom-pop", "Bedroom Pop", r"bedroom.?pop"),
        ("dream-pop", "Dream Pop", r"dream.?pop"), ("drift-phonk", "Drift Phonk", r"phonk"), ("trap-edm", "Trap EDM", r"trap edm|festival trap"),
        ("trance", "Trance", r"(?<!psy)\btrance\b"), ("psytrance", "Psytrance", r"psy.?trance|\bpsy\b"),
        ("eurodance", "Eurodance", r"eurodance"), ("future-house", "Future House", r"future.?house"),
        ("fidget-house", "Fidget House", r"fidget"), ("tropical-house", "Tropical House", r"tropical")]),
    ("live", "Live Sounds", [
        ("rock", "Rock", None), ("indie-rock", "Indie Rock", r"indie.?rock"), ("indie-dance", "Indie Dance", r"indie.?dance"),
        ("jazz", "Jazz", r"\bjazz"), ("blues", "Blues", r"\bblues\b"), ("gospel", "Gospel", r"gospel"),
        ("metal", "Heavy Metal", r"heavy.?metal"), ("funk", "Funk", r"(?<!brazilian )\bfunk\b"),
        ("dub", "Dub", r"(?<!uk )\bdub\b(?! ?techno)"), ("reggae", "Reggae", r"reggae(?!ton)"),
        ("afrobeat", "Afrobeat", r"afrobeat(?!s)"), ("folk", "Folk", r"\bfolk"), ("country", "Country", r"\bcountry\b"),
        ("classical", "Classical", None), ("shoegaze", "Shoegaze", r"shoegaze"), ("emo", "Emo", r"\bemo\b"),
        ("punk", "Punk", r"(?<!post-)(?<!post )\bpunk\b"), ("post-punk", "Post-Punk", r"post.?punk"),
        ("bossa-nova", "Bossa Nova", r"bossa"), ("cumbia", "Cumbia", r"cumbia"), ("salsa", "Salsa", r"salsa"),
        ("flamenco", "Flamenco", r"flamenco"), ("samba", "Samba", r"samba")]),
    ("bass-music", "Bass Music", [
        ("drum-and-bass", "Drum and Bass", r"drum.?(and|n|&).?bass|\bdnb\b|\bd&b\b"), ("jungle", "Jungle", r"\bjungle\b"),
        ("jump-up", "Jump Up DnB", r"jump.?up"), ("drumstep", "Drumstep", r"drumstep"),
        ("breakbeat", "Breakbeat", r"breakbeat|big.?beat"), ("dubstep", "Dubstep", r"(?<!tearout )dubstep|riddim"),
        ("tearout", "Tearout Dubstep", r"tearout"), ("grime", "Grime", r"\bgrime\b"),
        ("leftfield-bass", "Leftfield Bass", r"leftfield")]),
    ("electronic", "Electronic", [
        ("downtempo", "Downtempo", r"downtempo"), ("ambient", "Ambient", None), ("synthwave", "Synthwave", r"synthwave|retrowave"),
        ("chillout", "Chillout", r"chill.?out|\bchill\b"), ("indie-electronic", "Indie Electronic", r"indie.?electronic"),
        ("chillwave", "Chillwave", r"chillwave"), ("idm", "IDM", r"\bidm\b"), ("experimental", "Experimental", r"experimental"),
        ("industrial", "Industrial", None), ("chiptune", "Chiptune", r"chiptune|8.?bit"),
        ("trip-hop", "Trip Hop", r"trip.?hop"), ("footwork", "Footwork", r"footwork|juke")]),
    ("global", "Global", [
        ("african", "African", r"\bafrica"), ("asian", "Asian", r"\basia|east.?asia|japan|chinese|china\b"),
        ("brazilian", "Brazilian", r"brazil"), ("caribbean", "Caribbean", r"caribbean|calypso|soca"),
        ("indian", "Indian", r"\bindia|bollywood|desi"), ("latin-american", "Latin American", r"\blatin"),
        ("middle-eastern", "Middle Eastern", r"middle.?east|arabic|persian|turkish"),
        ("south-asian", "South Asian", r"south.?asia")]),
    ("cinematic", "Cinematic / FX", [
        ("cinematic", "Cinematic", r"cinematic|trailer|film score"), ("game-audio", "Game Audio", r"game.?audio|video.?game")]),
]
GROUPS = [(g, lbl) for g, lbl, _ in _G]
GENRES = {g: [(gid, lbl) for gid, lbl, _ in items] for g, _, items in _G}
GROUP_OF = {gid: g for g, _, items in _G for gid, _, _ in items}
LABEL = {g: lbl for g, lbl, _ in _G} | {gid: lbl for _, _, items in _G for gid, lbl, _ in items}
_RX = [(gid, re.compile(rx, re.I)) for _, _, items in _G for gid, _, rx in items if rx]
# only from the pack name (in subfolders these words usually describe the sound: "Ambient FX", "Metal")
_ROOT_ONLY = {"ambient": r"\bambient\b", "industrial": r"\bindustrial\b", "classical": r"\bclassical\b",
              "rock": r"\brock\b", "minimal-techno": r"minimal", "electro": r"\belectro\b"}
_ROOT_RX = [(gid, re.compile(rx, re.I)) for gid, rx in _ROOT_ONLY.items()]


def _norm(s: str) -> str:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    return re.sub(r"[_\.\[\]\(\)\-]+", " ", s).lower()


def from_text(text: str, root: bool = False) -> list[str]:
    """Genres from text. On overlap the more specific one wins ("tech house" is not also "house")."""
    t = _norm(text)
    found = [gid for gid, rx in _RX if rx.search(t)]
    if root:
        found += [gid for gid, rx in _ROOT_RX if rx.search(t) and gid not in found]
    return found


def folder_genres(rel_parts: list[str]) -> list[str]:
    """From subfolders only clear genre folders ("Drum Loops - By Genre/Latin", "Pack_Africa")."""
    for k in range(len(rel_parts) - 1, 0, -1):          # deepest first, not the pack root
        part = rel_parts[k]
        parent = rel_parts[k - 1].lower()
        g = from_text(part)
        if g and ("genre" in parent or "world" in parent or "style" in parent or len(_norm(part).split()) <= 3):
            return g
    return []


def load_cfg() -> dict:
    try:
        return json.loads(CFG.read_text())
    except (OSError, ValueError):
        return {}


def save_cfg(cfg: dict) -> None:
    CFG.parent.mkdir(parents=True, exist_ok=True)
    polut.write_atomic(CFG, json.dumps(cfg, ensure_ascii=False, indent=1, sort_keys=True))


def classify(paths: list[str], A, roots=None) -> tuple[list[set], list[bool], dict]:
    """-> (genres / sample (incl. groups), estimated / sample, packs {key: {genres, source, n}}).
    roots = the index's library roots. The pack is found from the folder tree (pakkaukset.find): directly
    under the library folder, or deeper when there are vendor or genre folders above the packs; its key
    is its path from the library folder ("TECH HOUSE/Toolroom Tech House Essentials"). A pack without a
    genre of its own takes it from the folders above it ("TECH HOUSE", or a folder the user gave genres to)."""
    import numpy as np
    import pakkaukset
    rp = polut.roots_per_path(paths, roots)
    pack_dirs, _ = pakkaukset.find(paths, roots)
    pack_of = [pakkaukset.pack_key(pk, r) for pk, r in zip(pack_dirs, rp)]
    packs: dict[str, dict] = {}
    for i, pk in enumerate(pack_of):
        packs.setdefault(pk, {"idx": [], "genres": [], "source": ""})["idx"].append(i)
    user = load_cfg().get("packs", {})
    for pk, d in packs.items():
        parts = pk.split("/")
        for k in range(len(parts), 0, -1):              # the pack itself, then the folders above it
            key = "/".join(parts[:k])
            if key in user:
                d["genres"], d["source"] = list(user[key]), "user"
                break
            g = from_text(parts[k - 1], root=True)
            if g:
                d["genres"], d["source"] = g, "name"
                break
    # analysis: the pack's average sound (loops weigh more) -> genre of the nearest named packs
    named = {pk: d for pk, d in packs.items() if d["genres"]}
    if named:
        def emb(d):
            v = np.asarray(A[d["idx"]], dtype=np.float32).mean(0)
            return v / (np.linalg.norm(v) + 1e-9)
        cents: dict[str, list] = {}
        for pk, d in named.items():
            for g in d["genres"]:
                cents.setdefault(g, []).append(emb(d))
        names = list(cents)
        C = np.stack([np.mean(cents[g], 0) for g in names])
        C /= np.linalg.norm(C, axis=1, keepdims=True)
        for pk, d in packs.items():                   # suggestion only: not used before confirmation
            if not d["genres"]:
                sims = C @ emb(d)
                d["suggest"] = [names[j] for j in np.argsort(-sims)[:3]]
    out, est = [], []
    for i, (p, r) in enumerate(zip(paths, rp)):
        d = packs[pack_of[i]]
        inside = p[len(pack_dirs[i]):].lstrip("/").split("/")[:-1]      # the folders inside the pack
        g = folder_genres([pack_of[i]] + inside)
        gs = g if g else d["genres"]
        out.append(set(gs) | {GROUP_OF[x] for x in gs if x in GROUP_OF})
        est.append(False)
    info = {pk: {"genres": d["genres"], "source": d["source"], "n": len(d["idx"]),
                 "suggest": d.get("suggest", [])} for pk, d in packs.items()}
    return out, est, info
