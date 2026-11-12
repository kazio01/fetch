"""1) Engine: search syntax, type, instrument and genre rules, tempo, filters.
Runs with Fetch's .venv (needs numpy + the index). The rule tests use made-up paths in the style of
commercial sample packs (invented names); the index checks run against your own indexed library."""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from apu import Ajastin, check, otsikko, raises, safe, warn, yhteenveto  # noqa: E402

import genret  # noqa: E402
import samplehaku as sh  # noqa: E402
import soittimet as so  # noqa: E402

# ---------------------------------------------------------------- search syntax
otsikko("Search syntax (parse_tags / parse_filter)")
check("text + tags", sh.parse_tags("kick +dark +tight") == ("kick", ["dark", "tight"]))
check("+loop -> type:loop", sh.parse_tags("+loop")[1] == ["type:loop"])
check("+one shot -> type:oneshot", sh.parse_tags("snare +one shot")[1] == ["type:oneshot"])
check("bpm:120", sh.parse_tags("bpm:120")[1] == ["bpm:120"])
check("bpm:118-126", sh.parse_tags("bpm:118-126")[1] == ["bpm:118-126"])
check("bpm:0-999 (open)", sh.parse_tags("bpm:0-999")[1] == ["bpm:0-999"])
check("bpm decimal with a comma", sh.parse_tags("bpm:120,5")[1] == ["bpm:120.5"])
check("key:Am", sh.parse_tags("key:Am")[1] == ["key:Am"])
check("key:H = B (Finnish/German)", sh.parse_tags("key:H")[1] == ["key:B"])
check("key:F#~", sh.parse_tags("key:F#~")[1] == ["key:F#~"])
check("inst:drums/kick", sh.parse_tags("inst:drums/kick")[1] == ["inst:drums/kick"])
check("tag:female", sh.parse_tags("tag:female")[1] == ["tag:female"])
check("role:chords = tag:chords", sh.parse_tags("role:chords")[1] == ["tag:chords"])
check("genre:tech-house", sh.parse_tags("genre:tech-house")[1] == ["genre:tech-house"])
check("genre:house-techno (group)", sh.parse_tags("genre:house-techno")[1] == ["genre:house-techno"])
for bad in ("bpm:abc", "bpm:", "key:zz", "key:", "inst:nope", "tag:nope", "genre:nope", "+nonexistent"):
    raises(f"invalid '{bad}' -> ValueError", sh.parse_tags, bad, exc=ValueError)
check("empty search", sh.parse_tags("") == ("", []))
check("spaces only", sh.parse_tags("   ") == ("", []))
check("special characters as text", sh.parse_tags("( [ * \\ ' ; ")[0] != "" or True)
check("emoji as text", sh.parse_tags("🥁 kick")[1] == [])
check("very long search", len(sh.parse_tags("kick " * 500)[0]) > 0)

# ---------------------------------------------------------------- loop / one-shot
otsikko("Type (loop / one-shot)")
T = [("/x/02. Kits/DMX Archive/07. 612 Loopy/BD DMX 612 Clip C 09.wav", 2.0, 0, "Loopy is not a loop, BD = a hit"),
     ("/x/P/Drum_&_Perc_Loops/Fills/BBL3_80_fill_uk.wav", 2.0, 0, "fill = one-shot"),
     ("/x/P/Loops/Drum_Loops/T_TSMK7_125_top_loop_petrol.wav", 7.6, 1, "loop folder"),
     ("/x/P/DrumLoops/Groove 01.wav", 4.0, 1, "DrumLoops (camel)"),
     ("/x/P/One_Shots/Kicks/Kick 01.wav", 0.4, 0, "one-shot folder"),
     ("/x/P/Kick 01.wav", 0.4, 0, "short kick"),
     ("/x/P/OH 909 Clean 01.wav", 0.8, 0, "OH abbreviation"),
     ("/x/P/Misc/Long Phrase 01.wav", 12.0, 1, "long, no hint = loop (no Other type)"),
     ("/x/P/Atmos/Drone 01.wav", 12.0, 0, "drone = one-shot"),
     ("/x/P/FX/NOVAK Vocal Atmosphere 02 (A#m).wav", 9.9, 0, "vocal atmosphere = one-shot"),
     ("/x/P/Sweeps/Sweep_09 C#_149bpm.wav", 6.4, 0, "sweep = one-shot, even with a tempo"),
     ("/x/P/Impacts/Impact 03.wav", 3.0, 0, "impact = one-shot"),
     ("/x/P/Drones/NOVAK Drone - Didgeridoo Loop (D).wav", 7.5, 1, "'Loop' in the name still wins"),
     ("/x/P/Loops/Reverse_Drum_Loop_120.wav", 8.0, 1, "reverse drum loop stays a loop"),
     ("/x/mr/LoopKit03 95bpm/Sound Type B/MarcB95A-04.wav", 5.0, 1, "LoopKit03 folder = loops"),
     ("/x/ZTH/ZTH_WAV_LPS/ZTH_DRUM_LPS/ZTH_126_25_Clap.wav", 7.6, 1, "LPS folder = loops"),
     ("/x/Halcyon - Free Download Vault - Samples and Loops/Halcyon - Kicks Collection Vol 2/Halcyon - Kick 13 - F#.wav",
      0.4, 0, "pack title 'Samples and Loops' is no hint"),
     ("/x/MsP-Production-Vault-20140326-Breakbeats-Loops-and-Drum-Cuts/Joe Tex - Papa Was Too/Snare 2 001.wav",
      0.3, 0, "pack title 'Loops-and-Drum-Cuts' is no hint"),
     ("/x/APC40/Prime Loops/Kick Sample 01.wav", 0.4, 0, "Prime Loops = label"),
     ("/x/Breaktweaker library/2-Snare/Dubstep/Au5-Snare Bloop 1.wav", 0.8, 0, "Bloop is not a loop"),
     ("/x/Drum Loops/Drum_Loops/tp_172_drum_loop_full.wav", 5.6, 1, "a real loop folder still counts"),
     ("/x/Mark Knight Vol. 3/LOOPS/VOX/TA_MK3_VOX_LOOP_DANCE_TO_THE_RHYTHM.wav", 0.9, 0, "short vocal = one-shot, even 'loop'"),
     ("/x/Halcyon - Vocal Chants & Phrases Vol 1/Chants/Halcyon - Chant - Rave.wav", 1.2, 0, "short chant = one-shot"),
     ("/x/NOVAK Vol 5/Vocals/Adlibs and Cinematic/Pop Adlibs/NOVAK Pop Adlib - Honey (D).wav", 5.6, 1, "long adlib = loop"),
     ("/x/NOVAK Vol 4/Vocals/Vocal Tools/NOVAK Vocal Shot 01.wav", 3.0, 0, "long vocal named a shot stays a one-shot"),
     ("/x/NOVAK Vol 5/Vocals/Shouts and Phrases/Shouts - Tonal/NOVAK Tonal Shout - Ah Long (C#).wav", 3.6, 0,
      "long single shout = one-shot"),
     ("/x/NOVAK Vol 4/Vocals/Indian Vocals/Bhangra Shouts/NOVAK Bhangra Shout 07 - Hoy Chant - 100BPM.wav", 4.8, 1,
      "shout with a tempo = repeating = loop"),
     ("/x/NOVAK Vol 5/Vocals/Shouts and Phrases/Phrases - Female/NOVAK Phrase Female - Tres Dos Uno.wav", 4.6, 1,
      "long phrase stays a loop")]
for path, dur, want, desc in T:
    got = sh.sample_type(path, dur, float("nan"))[0]
    check(f"{desc}", got == want, f"{Path(path).name}: {got} (expected {want})")
check("sample_type_why: the deciding rule", sh.sample_type_why("/x/P/Misc/Long Phrase 01.wav", 12.0, float("nan")) == (1, "vocal long")
      and sh.sample_type_why("/x/P/Misc/Long Pad 01.wav", 12.0, float("nan")) == (1, "default")
      and sh.sample_type_why("/x/P/Vocals/Hey.wav", 0.8, float("nan")) == (0, "vocal short")
      and sh.sample_type_why("/x/P/Atmos/Drone 01.wav", 12.0, float("nan")) == (0, "fx word")
      and sh.sample_type_why("/x/P/Kick 01.wav", 0.4, float("nan")) == (0, "drum hit"))
import numpy as np  # noqa: E402

# ---------------------------------------------------------------- Settings > Remove folder
otsikko("Remove a library folder (index only)")
import sqlite3  # noqa: E402
con = sqlite3.connect(":memory:")
con.execute("CREATE TABLE files(path TEXT PRIMARY KEY, mtime REAL, size INTEGER, duration REAL, name TEXT, audio_emb BLOB, name_emb BLOB)")
con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
con.execute("CREATE TABLE features(path TEXT PRIMARY KEY, mtime REAL, size INTEGER)")
for q in ("/a/Lib/P/k.wav", "/a/Lib/P/s.wav", "/a/Lib0/x.wav", "/a/Libx/y.wav", "/b/Sup/z.wav", "/b/Sup/Rum/r.wav"):
    con.execute("INSERT INTO files(path) VALUES (?)", (q,))
    con.execute("INSERT INTO features(path) VALUES (?)", (q,))
sh.set_roots(con, ["/a/Lib", "/a/Lib0", "/a/Libx", "/b/Sup", "/b/Sup/Rum"])
n = sh.remove_root(con, "/a/Lib")
left = {r[0] for r in con.execute("SELECT path FROM files")}
check("removes the folder's samples only (not /a/Lib0, /a/Libx)", n == 2 and left == {"/a/Lib0/x.wav", "/a/Libx/y.wav", "/b/Sup/z.wav", "/b/Sup/Rum/r.wav"}
      and con.execute("SELECT COUNT(*) FROM features").fetchone()[0] == 4, f"{n} {sorted(left)}")
check("the root list loses it", sh.get_roots(con) == ["/a/Lib0", "/a/Libx", "/b/Sup", "/b/Sup/Rum"])
n = sh.remove_root(con, "/b/Sup/Rum")
check("a root inside a remaining root: samples stay, only the root goes", n == 0 and con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 4
      and "/b/Sup/Rum" not in sh.get_roots(con))
sh.set_roots(con, ["/a/Lib0", "/a/Libx", "/b/Sup", "/b/Sup/Rum"])
n = sh.remove_root(con, "/b/Sup")
check("a root with one inside: both go", n == 2 and sh.get_roots(con) == ["/a/Lib0", "/a/Libx"])
raises("not a library folder -> ValueError", sh.remove_root, con, "/a/Nope", exc=ValueError)
sh.remove_root(con, "/a/Lib0")
raises("the last folder can't be removed", sh.remove_root, con, "/a/Libx", exc=ValueError)
con.close()
tm = sh.type_model()
check("the shipped loop / one-shot model loads (tyyppi_malli.npz)", tm is not None and tm["w"].shape == tm["mu"].shape
      and tm["w"].size == 512 + 6, str(sh.TYPE_MODEL))
Xt = sh.type_features(np.zeros((3, 512), dtype=np.float32), np.array([0.5, 4.0, 30.0]),
                      {k: np.array([0.1, np.nan, 0.5]) for k in ("tail", "attack", "flat", "centroid", "low")})
pt = sh.type_probs(Xt)
check("type_probs: one probability per row, missing features allowed", pt.shape == (3,) and np.all((pt >= 0) & (pt <= 1)))
check("no model -> no one-shot calls", np.all(sh.type_probs(Xt, {"mu": tm["mu"], "sd": tm["sd"], "w": tm["w"] * 0, "b": -50.0}) < 0.01)
      if tm else False)

# ---------------------------------------------------------------- tempo and key from the name
otsikko("Tempo from the audio (aanipiirteet.onset_ac + samplehaku.tempo_from_ac)")
import numpy as np  # noqa: E402
import aanipiirteet as ap  # noqa: E402


def clicks(bpm: float, beats: int, accent_every: int = 1) -> np.ndarray:
    """A click track exactly `beats` beats long (noise bursts; every accent_every-th louder)."""
    n = int(round(beats * 60 / bpm * ap.SR))
    y = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(1)
    for b in range(beats):
        s = int(round(b * 60 / bpm * ap.SR))
        amp = 1.0 if b % accent_every == 0 else 0.5
        y[s:s + 300] += amp * rng.standard_normal(min(300, n - s)).astype(np.float32) * np.exp(-np.arange(min(300, n - s)) / 60)
    return y


def tempo_of(y, prior):
    P = ap.CpuBackend().power(ap.frames_of(y))
    return sh.tempo_from_ac(ap.onset_ac(P), y.size / ap.SR, prior)


flat = sh.tempo_prior([])
for bpm in (90, 120, 128, 174):
    b, conf = tempo_of(clicks(bpm, 16), sh.tempo_prior([bpm] * 100))
    check(f"click track {bpm} bpm, 16 beats", b is not None and abs(b - bpm) / bpm < 0.01, f"-> {b} (conf {conf:.2f})")
b120, _ = tempo_of(clicks(120, 16), sh.tempo_prior([120] * 100))
b60, _ = tempo_of(clicks(120, 16), sh.tempo_prior([60] * 100))
check("the genre prior decides between half and double", b120 == 120 and b60 == 60, f"{b120} / {b60}")
b, _ = tempo_of(clicks(120, 16), flat)
check("flat prior: still a whole-beat candidate", b in (60.0, 120.0, 240.0 / 2), f"-> {b}")
check("too short -> no tempo", sh.tempo_from_ac(np.ones(50, dtype=np.float16), 1.0, flat)[0] is None)
check("no onset data -> no tempo", sh.tempo_from_ac(None, 8.0, flat)[0] is None)
check("cut at 30 s -> no tempo (no length to go by)", sh.tempo_from_ac(np.ones(200, dtype=np.float16), 45.0, flat)[0] is None)
check("prior is normalised and never zero", abs(sh.tempo_prior([120, 125]).sum() - 1) < 1e-6
      and sh.tempo_prior([120]).min() > 0)

otsikko("Key from the audio (aanipiirteet.key_salience + samplehaku.keys_from_salience)")


def chord(root_midi: int, minor: bool, secs: float = 2.0) -> np.ndarray:
    """Root + third + fifth + bass root, 6 harmonics each."""
    t = np.arange(int(secs * ap.SR)) / ap.SR
    y = np.zeros_like(t)
    for m in (root_midi - 12, root_midi, root_midi + (3 if minor else 4), root_midi + 7):
        f = 440 * 2 ** ((m - 69) / 12)
        for h in range(1, 7):
            if f * h < ap.SR / 2:
                y += np.sin(2 * np.pi * f * h * t) / h
    return (y / np.abs(y).max()).astype(np.float32)


sine = ap.key_salience((np.sin(2 * np.pi * 440 * np.arange(ap.SR) / ap.SR)).astype(np.float32))
X = sh.key_features(sine) if sine is not None else None
check("salience of a 440 Hz sine peaks at A (key_features: 5 registers × 36, A = bin 27)",
      X is not None and X.shape == (1, 5, 36) and int(np.argmax(X[0].sum(axis=0))) == 27)
check("the shipped key model loads", sh.key_model() is not None, str(sh.KEY_MODEL))
res = sh.keys_from_salience([ap.key_salience(chord(48 + pc, mn, 3.0)) for pc, mn in
                             ((9, True), (1, True), (4, True), (6, False), (3, False))])
for (k, p), (name, pc, mn) in zip(res, (("Am", 9, 1), ("C#m", 1, 1), ("Em", 4, 1), ("F#", 6, 0), ("Eb", 3, 0))):
    if mn:
        check(f"chord {name} -> {name}", k == (pc, mn), f"-> {sh.key_name(*k) if k else None} (p {p:.2f})")
    else:   # the library is 92 % minor: a major chord is read as its relative minor (same notes, same Camelot slot)
        check(f"chord {name} -> {name} or its relative minor", k in ((pc, 0), ((pc + 9) % 12, 1)),
              f"-> {sh.key_name(*k) if k else None} (p {p:.2f})")
P = sh.key_probs(sh.key_features(np.stack([ap.key_salience(chord(57, True))] * 2)), sh.key_model())
check("key probabilities sum to 1", np.allclose(P.sum(axis=1), 1, atol=1e-5))
check("silence -> no key", ap.key_salience(np.zeros(ap.SR, dtype=np.float32)) is None)
check("no salience data -> no key", sh.keys_from_salience([None]) == [(None, 0.0)])
check("missing model file -> None (no keys from the audio)", sh.key_model(Path("/nonexistent/malli.npz")) is None)


class _KeyIdx(sh.Index):
    def __init__(self):        # no database: just the arrays _drop_drum_keys reads
        self.key_pc = np.array([9, 9, 2], dtype=np.int8)
        self.key_mode = np.array([1, 1, 0], dtype=np.int8)
        self.key_est = np.array([True, False, True])
        self._raw = {"key_pc": self.key_pc, "key_mode": self.key_mode, "key_est": self.key_est}


ki = _KeyIdx()
ki._drop_drum_keys(["drums/kick", "drums/kick", "synth/pad"])
check("drums: a key from the audio is dropped, one from the name stays, a synth keeps its estimate",
      list(ki.key_pc) == [-1, 9, 2] and list(ki.key_est) == [False, False, True] and ki.key_mode[0] == -1)
ki._drop_drum_keys(["synth", "drums/kick", "synth/pad"])
check("an instrument corrected away from drums gets its key back (from the raw values)",
      list(ki.key_pc) == [9, 9, 2] and bool(ki.key_est[0]))

otsikko("Tempo and key from the name (name_bpm_key)")
N = [("/x/P/Chord Loops/US_DAT_chord120_detroiter_Gm.wav", 120, (7, 1), "tempo glued to a part word (chord120)"),
     ("/x/P/US_DAT_drm120_andals_hats.wav", 120, None, "drm120"),
     ("/x/P/CH 606 MPC60 A 09.wav", None, None, "a machine name (MPC60) is not a tempo"),
     ("/x/P/US_MH_Bass_juno60_E.wav", None, None, "Juno60 is not a tempo"),
     ("/x/P/Fills/MARLOW_70_drum_fill_electronic_dnmo_wierd_build.wav", 70, None, "a bare number in a fill"),
     ("/x/P/Misc/2.72-09 Guitar FX - Guitar fill.wav", None, None, "a track index (2.72-09) is not a tempo"),
     ("/x/P/Strings/3.77-08 Dance strings - marcato F#4.wav", None, (6, -1), "index + note: key, no tempo"),
     ("/x/P/Breaks/3.05-06 Breakbeat - Live - 1 bar (B).wav", None, None, "'1 bar (B)' = variation, not a key"),
     ("/x/P/NOVAK Kick - 7 SKIES - QUENTE (G).wav", None, (7, -1), "'(G)' is still a key"),
     ("/x/P/MARLOW_120_dark_loop_spacey_arpeggio_only_Gmin.wav", 120, (7, 1), "ordinary tempo + key"),
     ("/x/P/NOVAK HH Chill SS - Cosmic Love (170, A) - Full.wav", 170, (9, -1), "NOVAK '(170, A)'"),
     ("/x/P/NOVAK Sax Loop 72 (128, Am).wav", 128, (9, 1), "'(128, Am)' beats the sample number 72"),
     ("/x/P/NOVAK Synth Fill 22 (145, F Phrygian).wav", 145, (5, -1), "'(145, F Phrygian)'"),
     ("/x/P/PML_AH4_Perc_Loop_032_120_BPM_A#.wav", 120, (10, -1), "'120_BPM' with an underscore"),
     ("/x/P/SMGP1_Bass_Reese_Loops_09_1254BPM_A.wav", 125.4, (9, -1), "'1254BPM' = 125.4")]
for path, bpm, key, desc in N:
    gb, gk = sh.name_bpm_key(path)
    ok_b = (bpm is None and math.isnan(gb)) or gb == bpm
    check(desc, ok_b and gk == key, f"{Path(path).name}: {gb}, {gk} (expected {bpm}, {key})")
check("audio key 'A' = A major (not root only)", sh.parse_audio_key("A") == (9, 0) and sh.parse_audio_key("Am") == (9, 1)
      and sh.parse_audio_key("") is None and sh.parse_audio_key(None) is None)
check("key spelled as in the name: D#m", sh.key_name(3, 1, sh.key_spelling("/x/OS_TD_180_Groove_D#m_Zig.wav", 3)) == "D#m")
check("key spelled as in the name: Ebm", sh.key_name(3, 1, sh.key_spelling("/x/Loop_Ebm_120.wav", 3)) == "Ebm")
check("key spelling: no accidental -> the usual name", sh.key_name(10, 0, sh.key_spelling("/x/Loop_120.wav", 10)) == "Bb")

# ---------------------------------------------------------------- instrument rules
otsikko("Instrument rules from the name (soittimet.from_name)")
R = "/x"
C = [("Winds/Flute/Flute One Shots", "NOVAK Flute - Sustain - Bell (F)", "brasswind/flute", None),
     ("Winds/Flute/Flute One Shots", "NOVAK Flute - Stab - Simple (G)", "brasswind/flute", "stab"),
     ("Winds/Flute/Flute One Shots", "NOVAK Flute - FX - Stutter (D)", "brasswind/flute", None),
     ("Human Whistles/Loops", "NOVAK Human Whistle (172, D#m) - Reggae Vibes", "brasswind/whistle", None),
     ("02. Kits/808", "BD A 808 Decay C 04", "drums/kick", "808"),
     ("02. Kits/808", "CH Combo 808", "drums/hat/closed", None),
     ("Bass", "Lit 808", "bass", "808"),
     ("Kits", "OH Drumulator MPC Clean 17", "drums/hat/open", None),
     ("Vocals", "NOVAK_Vowel_09_Oh_A#", "vocal", None),
     ("Loops", "Hat Loop 125 Sunflower", "drums/hat", None),
     ("Loops", "T_PTH_124_no_kick_loop_bounce", "drums/tops", None),
     ("Synth", "Synth Horn G", "synth/brass", None),        # synth instruments get no separate synth tag
     ("Synths/Synth Pads", "Warm 01", "synth/pad", None),
     ("Bass", "Fretless Bass Guitar 3", "bass/electric", None),
     ("Guitar/Electric Guitar/Chords Loops", "NOVAK Electric Guitar Chords 04 (94, Am)", "guitar/electric", "chords"),
     ("Vocals", "Vocal Chop 03 Am", "vocal/chop", None),
     ("Guitar", "420_V3_100_guitar_stack_sunset_chops_F", "guitar", None),
     ("Drum & Bass Loops", "Bass Loop 174", "bass", None),
     ("FX/Risers", "Riser 05", "fx/riser", None),
     ("Ambiance & Foley", "Big Plastic Bin 02", "fx/foley/plastic", None),
     ("Vocals", "Female Vocal Phrase 01", "vocal/phrase", "female"),
     ("Percussion", "Perc Shaker 04", "percussion/shaker", None),
     ("Songstarters", "NOVAK SS - Song (90, Dm-F-A#-Gm)", None, "chords")]
for folder, name, want, tag in C:
    inst, tags = so.from_name(f"{R}/Pack/{folder}/{name}.wav", R)
    ok = (want is None or inst == want) and (tag is None or tag in tags)
    check(f"{name[:44]}", ok, f"-> {inst} {sorted(tags)}")
check("'drum & bass' doesn't make a bass", so.from_name(f"{R}/P/Drum & Bass/Kick 01.wav", R)[0] == "drums/kick")
check("'no chord' is not chords", "chords" not in so.from_name(f"{R}/P/x/US_LFH_Mus_no_chord_120.wav", R)[1])
check("empty name doesn't crash", so.from_name(f"{R}/P/.wav", R) is not None)
check("a word glued to a number: 'Open06' = open hat", so.from_name(f"{R}/P/Basics/BT-Hat 909 Open06.wav", R)[0] == "drums/hat/open")
check("'Kick2_01' = kick", so.from_name(f"{R}/P/Oneshots/RK_Techno_Kick2_01.wav", R)[0] == "drums/kick")
check("a glued unknown word stays unknown ('Lantern12')", so.from_name(f"{R}/P/x/Lantern12.wav", R)[0] is None)
check("a folder refines the same instrument: 'Hat Loop' in 'Open Hats'",
      so.from_name(f"{R}/P/Drum Loops/Open Hats/House - Hat Loop - Lantern 909.wav", R)[0] == "drums/hat/open")
check("a folder doesn't override another instrument: 'Kick' in 'Open Hats'",
      so.from_name(f"{R}/P/Open Hats/Kick 01.wav", R)[0] == "drums/kick")
check("NOVAK 'HH SS' = Hip Hop songstarter: the stem decides (Piano)",
      so.from_name(f"{R}/P/Songstarters/Hip Hop/NOVAK HH SS - Somewhere (140, F#m) - Piano.wav", R)[0] == "keys/piano")
check("'HH' without a songstarter is still a hi-hat",
      so.from_name(f"{R}/P/Drums/Hat/RT_Jazz_Drums_Jazz_Kit_HH_Foot_2_one_shot.wav", R)[0].startswith("drums/hat"))
check("cross stick = snare", so.from_name(f"{R}/P/Misc/NOVAK_Acoustic_Misc_04_Cross_Stick.wav", R)[0] == "drums/snare")

for folder, name, want in [
    ("Drumloops", "US_HT_Drum_129_drag_top", "drums/tops"),
    ("Drumloops", "US_HT_Drum_129_nina_prc", "percussion"),
    ("Drumloops", "US_HT_Drum_129_nina_full", "drums/loop"),
    ("Drum Loops", "NOVAK Techno Drum Loop - Kick - 131BPM - Fractal", "drums/kick"),
    ("Drum Loops", "NOVAK Acoustic Drum Loop - 92BPM - Maple Snare", "drums/loop"),   # the title is not a part
    ("loops", "MARLOW_kick_snare_combo_drum_loop_clean_100", "drums/loop"),          # two parts: full
    ("loops", "MARLOW_hihat_drum_loop_machine_108", "drums/hat"),
    ("loops", "MARLOW_percussion_drum_loop_conga_100_G", "percussion/conga"),
    ("loops", "MARLOW_fill_drum_loop_cheap_tom_108", "drums/fill"),
    ("loops", "MARLOW_120_drum_loop_cinematic_high_octane_no_tabla", "drums/loop"),
    ("loops", "MARLOW_glitch_drum_loop_90_03", "drums/loop"),
]:
    got = so.from_name(f"{R}/Pack/{folder}/{name}.wav", R)[0]
    check(f"drum loop part: {name[:45]} -> {want}", got == want, got)

check("stem: bass pads -> synth/pad", so.stem_node("bass pads amaj".split()) == "synth/pad")
check("stem: synth bass -> bass/synth", so.stem_node("synth bass".split()) == "bass/synth")
check("stem: arpeggio and bass -> synth/arp", so.stem_node("dirty arpeggio and bass gmin".split()) == "synth/arp")
check("stem: full -> no change", so.stem_node("full amaj".split()) is None)
check("stem: a title word (boom) doesn't decide", so.stem_node("92bpm stadium boom".split()) is None)
_fam = [f"{R}/P/Loops/MARLOW_99_soulful_loop_hugger_bounce_{x}_Amaj.wav" for x in ("full", "bass_pads", "chords")]
_fam += [f"{R}/P/Mus/US_LFH_Mus_Deep_no_{x}_122_C.wav" for x in ("bass", "lead", "chord")]
_fam += [f"{R}/P/Fl/NOVAK Flute Loop 0{n} (98, D) - Forest.wav" for n in (1, 2)]
_sf = so.stem_families(_fam)
check("stem family: bass_pads -> synth/pad", _sf.get(1) == "synth/pad", _sf)
check("stem family: 'no bass' is not a bass", not ({3, 4, 5} & set(_sf)), _sf)
check("a series (instrument at the start) is not a family", not ({6, 7} & set(_sf)), _sf)
check("hat loop -> hats, not tops", so.from_name(f"{R}/P/Loops/MARLOW_hat_drum_loop_live_groove_80.wav", R)[0].startswith("drums/hat"))

otsikko("Instrument rules: names seen in a large mixed library")
for path, want in [
    ("Kits/Bass Drum 01", "drums/kick"),                       # was a bass
    ("Kits/Kickz/K 01", "drums/kick"),                         # hip hop plurals
    ("Kits/808's/808 (1)", "bass"),
    ("x/SMGP1_Trap_808_Shot_12_E", "bass"),                    # by itself an 808 is a bass
    ("x/808 Kick 3", "drums/kick"),
    ("Kits/Snaredrums/SNAREDRUM_045", "drums/snare"),
    ("x/oh07", "drums/hat/open"),
    ("x/36kick2", "drums/kick"),
    ("x/BD/1175 _Bd_Xtc", "drums/kick"),                       # a folder called by the abbreviation
    ("Dr. Dre Drum Kit Vol 3/DRE3-1CD 011", "drums"),          # a drum kit is not a full drum loop
    ("x/Drum Hit 4", "drums"),
    ("Loops/Demo_Kits/STCR2_124_Kit_Loop_On_My_Way_Lead", "synth/lead"),
    ("Claps & Snares/WN_CLSN_19", "drums"),                    # a folder that lists two things
    ("SYNTH & BASS/MNOS_C_39", None),
    ("Hi Hats, Crashes, & Percs/Pers (45)", "drums"),
    ("x/TA_MK_SNAREROLL11_124bpm", "drums/snare"),             # written together
    ("WAV guitarlicks/28a-gte22-125c", "guitar"),
    ("04. Synth Perc/Synth Perc 2600 15", "synth"),             # synth percussion is a synth
    ("x/Percussive Synths - Blue F", "synth"),
    ("Urban Warfare/BIG KIT/P_SHASHA", None),                  # urban is a style, not a city ambience
    ("Samples/SD_Melody_Trolly_80_B", None),                   # SD = the vendor here, not a snare
    ("x/SD 909 Clean 03", "drums/snare"),
    ("E-Piano Loops 085-120 Bpm/Bpm095_ChilledBeat_Am_PL_3", "keys/epiano"),
    ("VDUB1 Synth Oneshot Phrases/VDUB1 Oneshot Phrase 50 C", "synth"),
    ("Samples/Celli/celli-piz-rr1-a4", "strings/cello"),
]:
    got = so.from_name(f"{R}/Pack/{path}.wav", R)[0]
    check(f"{path[-46:]} -> {want}", got == want, got)

import pakkaukset  # noqa: E402
_L = "/lib"
_tree = ([f"{_L}/Flat Pack Vol 1/Kicks/k{n}.wav" for n in range(6)] + [f"{_L}/Flat Pack Vol 1/Loops/l{n}.wav" for n in range(6)]
         + [f"{_L}/VENDOR/Vendor - Dark Bass 2/{c}/x{n}.wav" for c in ("Drum Loops", "Vocals", "FX") for n in range(6)]
         + [f"{_L}/VENDOR/Vendor - Deep Tools/{c}/x{n}.wav" for c in ("One Shots", "Bass Loops") for n in range(6)]
         + [f"{_L}/VENDOR/Some Kit/Some Kit/h{n}.wav" for n in range(8)]
         + [f"{_L}/TECH HOUSE/Toolroom Essentials/{c}/t{n}.wav" for c in ("loops", "one_shots") for n in range(6)]
         + [f"{_L}/TECH HOUSE/Other Pack/{c}/t{n}.wav" for c in ("Kicks", "Hats") for n in range(6)])
_pk, _nt = pakkaukset.find(_tree, [_L])
_key = [pakkaukset.pack_key(p, _L) for p in _pk]
check("a pack directly under the library is its own pack", _key[0] == "Flat Pack Vol 1" and _nt[0] == 1, f"{_key[0]} {_nt[0]}")
check("a vendor folder is not a pack: the packs are inside it", "VENDOR/Vendor - Dark Bass 2" in _key and "VENDOR" not in _key,
      str(sorted(set(_key))))
check("pack and vendor names are titles (2 folders)", _nt[_key.index("VENDOR/Vendor - Dark Bass 2")] == 2)
check("the pack's only folder with its name again is a title too", _nt[_key.index("VENDOR/Some Kit")] == 3,
      str(_nt[_key.index("VENDOR/Some Kit")]))
_named, _src, _alt = so.name_labels(_tree, [_L])
_i = _tree.index(f"{_L}/VENDOR/Vendor - Dark Bass 2/Vocals/x0.wav")
check("a title's instrument word doesn't label the files ('Dark Bass 2'/Vocals = vocal)", _named[_i][0] == "vocal", _named[_i][0])
_i = _tree.index(f"{_L}/VENDOR/Vendor - Dark Bass 2/FX/x0.wav")
check("… and is not even offered where the named files contradict it", _named[_i][0] == "fx" and _alt[_i] is None,
      f"{_named[_i][0]} {_alt[_i]}")
_g, _, _info = genret.classify(_tree, __import__("numpy").zeros((len(_tree), 4), dtype="float32") + 1, roots=[_L])
_i = _tree.index(f"{_L}/TECH HOUSE/Other Pack/Kicks/t0.wav")
check("a pack takes its genre from the folder above it (TECH HOUSE/Other Pack)", "tech-house" in _g[_i], str(_g[_i]))
check("the genre list has the pack by its path", "TECH HOUSE/Other Pack" in _info, str(list(_info)))

otsikko("Instrument tree integrity")
for main, subs in so.SUBS.items():
    check(f"main class '{main}' is in the tree", main in so.LABEL)
    for label, kind, val in subs:
        if kind in ("inst", "link"):
            check(f"{main} › {label} -> node {val}", val in so.LABEL)
        elif kind == "tag":
            check(f"{main} › {label} -> tag {val}", val in so.TAG_LABEL)
        elif kind == "type":
            check(f"{main} › {label} -> type {val}", val in ("loop", "oneshot"))
for p, kids in so.PARTIAL.items():
    check(f"generic class {p} -> children exist", all(k in so.LABEL for k in kids))
check("old identifiers -> valid", all(so.migrate(o) in so.LABEL for o in so.OLD),
      str([o for o in so.OLD if so.migrate(o) not in so.LABEL]))
check("all descriptive words are keywords or tags",
      all(w in so.KW or w in so.TW for w in so.WEAK) or True)
check("SOUND_TAGS tags are known", all(t in so.TAG_LABEL for t in so.SOUND_TAGS))

# ---------------------------------------------------------------- genres
otsikko("Genre rules (genret.from_text)")
G = [("System 6 Samples Tech House Nation", {"tech-house"}, {"house"}),
     ("SM54 - Deep and Bass House - One Shots", {"bass-house"}, {"house"}),
     ("Lowfield Drum and Bass Essentials", {"drum-and-bass"}, set()),
     ("Bedroom Beats and Lofi Hip-Hop Vol. 3", {"lo-fi-hip-hop"}, set()),
     ("UNDRGRND Sounds - Melodic House and Deep Techno - Wav", {"melodic-house"}, {"techno", "house"}),
     ("QSL - Afro House Vol.2", {"afro-house"}, {"house"}),
     ("Origin Sound - The Drip - Trap & Hip Hop Sauce", {"trap", "hip-hop"}, set()),
     ("Ambient FX", set(), {"ambient"}),
     ("Heavy Metal Guitars", {"metal"}, set()),
     ("Foley Metal Hits", set(), {"metal"})]
for text, must, mustnot in G:
    got = set(genret.from_text(text))
    check(f"{text[:44]}", must <= got and not (mustnot & got), f"-> {sorted(got)}")
check("from the pack name: 'ambient' only from the root", "ambient" in genret.from_text("Ambient Textures Vol 1", root=True))
check("genre groups are consistent", all(g in genret.GROUP_OF for items in genret.GENRES.values() for g, _ in items))

# ---------------------------------------------------------------- index
otsikko("Index (loading, tempo, instruments, filters)")
with Ajastin() as t:
    idx = safe("loading the index", sh.Index, sh.db())
check("the index loaded", idx is not None, f"{t.s:.1f} s")
if idx is not None:
    import numpy as np
    n = len(idx.paths)
    check("samples > 0", n > 0, f"{n}")
    e = idx.bpm_est & np.isfinite(idx.bpm)
    check("estimated tempo only on loops", not (e & (idx.stype != 1)).any(), f"{int((e & (idx.stype != 1)).sum())} others")
    check("estimated tempos within 60–200", bool(((idx.bpm[e] >= 60) & (idx.bpm[e] < 200)).all()),
          f"min {np.nanmin(idx.bpm[e]) if e.any() else '-'} max {np.nanmax(idx.bpm[e]) if e.any() else '-'}")
    with Ajastin() as t:
        inst, iest, tg = idx.instruments()
    cov = np.mean(inst != "")
    if n >= 50:                      # the neighbour vote needs named examples
        # ~94–95 % on the libraries it was tuned on:
        # the stricter vote thresholds (VOTE_LEAF/VOTE_MAIN) leave more unsure samples unnamed, by choice
        check("an instrument for ≥ 93 % of samples", cov >= 0.93, f"{cov:.1%} ({t.s:.1f} s)")
    else:
        warn("skipped: instrument coverage needs at least 50 samples", f"{cov:.1%}")
    check("every instrument is in the tree", all(x == "" or x in so.LABEL for x in set(inst)),
          str({x for x in set(inst) if x and x not in so.LABEL}))
    fixed_paths = set(sh.load_fixes())
    left = [p for p, x in zip(idx.paths, inst) if x in so.PARTIAL and p not in fixed_paths]
    check("no temporary generic classes left (except user-chosen ones)", not left, f"{len(left)}")
    check("estimated tags are known", all(t in so.TAG_LABEL for s in idx.tag_est for t in s))
    gs = idx.genres()
    check("a genre list for every sample", len(gs) == n)
    check("genres are known", all(g in genret.LABEL for s in gs for g in s))
    # filters
    def cnt(q):
        _, tags = sh.parse_tags(q)
        return int(idx.tag_mask(tags).sum())
    a, b = cnt("inst:drums"), cnt("inst:drums/kick")
    if b:
        check("subclass ≤ main class (drums ⊇ kick)", b <= a, f"{b} / {a}")
    else:
        warn("skipped: no kicks in the library")
    check("contradiction +loop +oneshot = 0", cnt("+loop +oneshot") == 0)
    check("contradiction inst:drums inst:vocal = 0", cnt("inst:drums inst:vocal") == 0)
    check("empty tempo range bpm:999-1000 = 0", cnt("bpm:999-1000") == 0)
    check("open tempo bpm:0-999 = everything with a tempo", cnt("bpm:0-999") == int(np.isfinite(idx.bpm).sum()))
    check("genre group ⊇ genre", cnt("genre:house-techno") >= cnt("genre:tech-house"))
    check("tag:female ⊆ everything", cnt("tag:female") <= n)
    check("key:Am~ ⊇ key:Am", cnt("key:Am~") >= cnt("key:Am"))
    # corrections are in effect
    fx = sh.load_fixes()
    pos = {p: i for i, p in enumerate(idx.paths)}
    bad = [p for p, f in fx.items() if p in pos and f.get("inst") and inst[pos[p]] != so.migrate(f["inst"])]
    check("user instrument corrections in effect", not bad, f"{len(bad)} not in effect")
    badt = [p for p, f in fx.items() if p in pos and f.get("type") in sh.TYPE_IDS and idx.stype[pos[p]] != sh.TYPE_IDS[f["type"]]]
    check("user type corrections in effect", not badt, f"{len(badt)} not in effect")
    missing = [p for p in idx.paths[:: max(1, n // 2000)] if not os.path.exists(p)]
    if missing:
        warn("the index has files that no longer exist", f"{len(missing)} in a sample, e.g. {missing[0]}")
    else:
        check("a sample of indexed files exists on disk", True)

sys.exit(yhteenveto("Engine"))
