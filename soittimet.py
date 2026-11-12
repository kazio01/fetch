"""
Instrument classification for Fetch – Splice's structure + a few additions.

Every sample has
  * an instrument (tree, one): "keys/piano", "drums/hat/closed", "percussion/shaker", "fx/foley/metal"
  * style tags (many): chords, melody, stab, distorted, 808, female …  ("Flute – Stab" = flute + stab)

Splice's subcategories are shown under the instrument: some are instruments (Piano, Organ), some tags
(Chords, Stabs) or links to another instrument (Synth › Bass = Bass › Synth bass) – SUBS.

1) From the name: the file name first, then folders from the deepest upwards (the pack name is not read,
   because it often contains a genre). An instrument word beats a descriptive word.
2) The rest from the audio: the nearest named neighbours vote with CLAP audio vectors (main class ~85 %
   correct in a leave-one-pack-out test). A generic class from the name ("Hat", "Cymbal") is refined the same way.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- instrument tree
TREE: list[tuple[str, str]] = [
    ("drums", "Drums"),
    ("drums/kick", "Kicks"), ("drums/snare", "Snares"),
    ("drums/hat", "Hats"), ("drums/hat/closed", "Closed hats"), ("drums/hat/open", "Open hats"),
    ("drums/clap", "Claps"), ("drums/tom", "Toms"),
    ("drums/cymbal", "Cymbals"), ("drums/cymbal/ride", "Rides"), ("drums/cymbal/crash", "Crashes"),
    ("drums/loop", "Full drum loops"), ("drums/tops", "Tops"), ("drums/break", "Breaks"), ("drums/fill", "Fills"),
    ("percussion", "Percussion"),
    ("percussion/shaker", "Shakers"), ("percussion/conga", "Congas"), ("percussion/bongo", "Bongos"),
    ("percussion/tambourine", "Tambourines"), ("percussion/cowbell", "Cowbells"),
    ("percussion/woodblock", "Woodblocks & claves"), ("percussion/bell", "Bells"),
    ("percussion/djembe", "Djembe"), ("percussion/timbale", "Timbales"), ("percussion/rim", "Rims"),
    ("percussion/triangle", "Triangles"), ("percussion/tabla", "Tabla & hand drums"),
    ("percussion/other", "Other percussion"),
    ("bass", "Bass"),
    ("bass/synth", "Synth bass"), ("bass/sub", "Sub bass"), ("bass/electric", "Electric bass"),
    ("bass/acoustic", "Acoustic bass"),
    ("synth", "Synth"),
    ("synth/lead", "Leads"), ("synth/pad", "Pads"), ("synth/pluck", "Plucks"), ("synth/arp", "Arps"),
    ("synth/stab", "Stabs"), ("synth/brass", "Synth brass"), ("synth/bell", "Synth bells"),
    ("synth/keys", "Synth keys"), ("synth/fx", "Synth FX"),
    ("brasswind", "Brass & Woodwinds"),
    ("brasswind/sax", "Saxophone"), ("brasswind/trumpet", "Trumpet"), ("brasswind/trombone", "Trombone"),
    ("brasswind/horn", "Horns & tuba"), ("brasswind/flute", "Flute"), ("brasswind/clarinet", "Clarinet & oboe"),
    ("brasswind/harmonica", "Harmonica"), ("brasswind/whistle", "Whistle"),
    ("brasswind/world", "World winds"),
    ("keys", "Keys"),
    ("keys/piano", "Piano"), ("keys/epiano", "Electric piano"), ("keys/wurlitzer", "Wurlitzer"),
    ("keys/organ", "Organ"), ("keys/clavinet", "Clavinet"), ("keys/mallets", "Mallets & bells"),
    ("guitar", "Guitar"),
    ("guitar/electric", "Electric guitar"), ("guitar/acoustic", "Acoustic guitar"),
    ("guitar/world", "Sitar, oud & world"),
    ("strings", "Strings"),
    ("strings/violin", "Violin"), ("strings/viola", "Viola"), ("strings/cello", "Cello"),
    ("strings/bass", "Double bass"), ("strings/harp", "Harp"), ("strings/ensemble", "String ensemble"),
    ("vocal", "Vocals"),
    ("vocal/phrase", "Phrases"), ("vocal/chop", "Chops"), ("vocal/choir", "Choir"),
    ("vocal/adlib", "Shouts & adlibs"), ("vocal/spoken", "Spoken & dialogue"), ("vocal/fx", "Vocal FX"),
    ("fx", "FX"),
    ("fx/noise", "Noise"), ("fx/riser", "Risers"), ("fx/downer", "Downers"), ("fx/sweep", "Sweeps & whooshes"),
    ("fx/impact", "Impacts"), ("fx/atmos", "Atmospheres & textures"), ("fx/reverse", "Reverse"),
    ("fx/field", "Field recordings"), ("fx/glitch", "Glitch"),
    ("fx/foley", "Foley"),
    ("fx/foley/paper", "Paper"), ("fx/foley/wood", "Wood"), ("fx/foley/plastic", "Plastic"),
    ("fx/foley/metal", "Metal"), ("fx/foley/concrete", "Concrete & stone"), ("fx/foley/fire", "Fire"),
    ("fx/foley/water", "Water"),
    ("fx/ambience", "Ambience"),
    ("fx/ambience/indoor", "Indoor"), ("fx/ambience/outdoor", "Outdoor & nature"),
    ("fx/ambience/city", "City & industry"), ("fx/ambience/weather", "Weather"),
    ("fx/animals", "Animals"), ("fx/human", "Human"), ("fx/household", "Household"),
    ("fx/scifi", "Science fiction"), ("fx/machines", "Machines & devices"),
    ("fx/transport", "Transportation"), ("fx/warfare", "Warfare"),
]
LABEL = dict(TREE)
IDS = [t for t, _ in TREE]
MAINS = [t for t in IDS if "/" not in t]

# ---------------------------------------------------------------- style tags (many per sample)
TAG_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Style", [("chords", "Chords"), ("melody", "Melody"), ("riff", "Riffs"), ("lead", "Leads"),
               ("stab", "Stabs"), ("pad", "Pads"), ("pluck", "Plucks"), ("arp", "Arps"),
               ("staccato", "Staccato"), ("rhythm", "Rhythm"), ("ensemble", "Ensemble"),
               ("orchestral", "Orchestral"), ("classical", "Classical")]),
    ("Character", [("synth", "Synth"), ("analog", "Analog"), ("acid", "Acid"), ("saw", "Saw"),
                   ("wobble", "Wobble"), ("pulse", "Pulse"), ("distorted", "Distorted"), ("clean", "Clean"),
                   ("acoustic", "Acoustic"), ("808", "808"), ("reverse", "Reverse")]),
    ("Voice", [("female", "Female"), ("male", "Male"), ("vocoder", "Vocoder"), ("scream", "Screams"),
               ("whisper", "Whispers")]),
]
TAG_LABEL = {t: l for _, ts in TAG_GROUPS for t, l in ts}
ROLES = [("chords", "Chords"), ("melody", "Melody")]       # independent of the top-row instrument

# Splice's subcategories under an instrument: (name, kind, value)
#   inst = node in the instrument tree, tag = instrument + tag, link = another instrument's node, type = instrument + type
SUBS: dict[str, list[tuple[str, str, str]]] = {
    "drums": [("Kicks", "inst", "drums/kick"), ("Snares", "inst", "drums/snare"), ("Hats", "inst", "drums/hat"),
              ("Claps", "inst", "drums/clap"), ("Toms", "inst", "drums/tom"), ("Cymbals", "inst", "drums/cymbal"),
              ("Breaks", "inst", "drums/break"), ("Fills", "inst", "drums/fill"), ("Acoustic", "tag", "acoustic"),
              ("808", "tag", "808"), ("Full loops", "inst", "drums/loop"), ("Tops", "inst", "drums/tops")],
    "percussion": [("Shakers", "inst", "percussion/shaker"), ("Conga", "inst", "percussion/conga"),
                   ("Grooves", "type", "loop"), ("Tambourine", "inst", "percussion/tambourine"),
                   ("Bongos", "inst", "percussion/bongo"), ("Cowbells", "inst", "percussion/cowbell"),
                   ("Woodblock", "inst", "percussion/woodblock"), ("Bells", "inst", "percussion/bell"),
                   ("Djembe", "inst", "percussion/djembe"), ("Timbales", "inst", "percussion/timbale"),
                   ("Rims", "inst", "percussion/rim"), ("Triangle", "inst", "percussion/triangle"),
                   ("Tabla", "inst", "percussion/tabla"), ("Other", "inst", "percussion/other")],
    "synth": [("Bass", "link", "bass/synth"), ("Leads", "inst", "synth/lead"), ("Pads", "inst", "synth/pad"),
              ("Arp", "inst", "synth/arp"), ("Stabs", "inst", "synth/stab"), ("Chords", "tag", "chords"),
              ("Plucks", "inst", "synth/pluck"), ("Analog", "tag", "analog"), ("FX", "inst", "synth/fx"),
              ("Melody", "tag", "melody"), ("Brass", "inst", "synth/brass"), ("Bells", "inst", "synth/bell"),
              ("Keys", "inst", "synth/keys")],
    "brasswind": [("Saxophone", "inst", "brasswind/sax"), ("Trumpet", "inst", "brasswind/trumpet"),
                  ("Trombone", "inst", "brasswind/trombone"), ("Flute", "inst", "brasswind/flute"),
                  ("Ensemble", "tag", "ensemble"), ("Synth", "link", "synth/brass"), ("Riffs", "tag", "riff"),
                  ("Stabs", "tag", "stab"), ("Pads", "tag", "pad"), ("Harmonica", "inst", "brasswind/harmonica"),
                  ("Horns", "inst", "brasswind/horn"), ("Clarinet", "inst", "brasswind/clarinet"),
                  ("Whistle", "inst", "brasswind/whistle"), ("World", "inst", "brasswind/world")],
    "keys": [("Piano", "inst", "keys/piano"), ("E-piano", "inst", "keys/epiano"),
             ("Wurlitzer", "inst", "keys/wurlitzer"), ("Organ", "inst", "keys/organ"),
             ("Synth", "link", "synth/keys"), ("Clavinet", "inst", "keys/clavinet"), ("Chords", "tag", "chords"),
             ("Melody", "tag", "melody"), ("Stabs", "tag", "stab"), ("Classical", "tag", "classical"),
             ("Mallets", "inst", "keys/mallets")],
    "guitar": [("Electric", "inst", "guitar/electric"), ("Acoustic", "inst", "guitar/acoustic"),
               ("Clean", "tag", "clean"), ("Distorted", "tag", "distorted"), ("Synth", "tag", "synth"),
               ("Chords", "tag", "chords"), ("Leads", "tag", "lead"), ("Melody", "tag", "melody"),
               ("Riffs", "tag", "riff"), ("Rhythm", "tag", "rhythm"), ("World", "inst", "guitar/world")],
    "bass": [("Synth", "inst", "bass/synth"), ("Sub", "inst", "bass/sub"), ("Analog", "tag", "analog"),
             ("Acoustic", "inst", "bass/acoustic"), ("Electric", "inst", "bass/electric"), ("Acid", "tag", "acid"),
             ("Saw", "tag", "saw"), ("Distorted", "tag", "distorted"), ("Wobble", "tag", "wobble"),
             ("Pulse", "tag", "pulse"), ("808", "tag", "808")],
    "fx": [("Noise", "inst", "fx/noise"), ("Risers", "inst", "fx/riser"), ("Downers", "inst", "fx/downer"),
           ("Sweeps", "inst", "fx/sweep"), ("Impacts", "inst", "fx/impact"), ("Atmos", "inst", "fx/atmos"),
           ("Reverse", "inst", "fx/reverse"), ("Field rec.", "inst", "fx/field"), ("FX vocals", "link", "vocal/fx"),
           ("Glitch", "inst", "fx/glitch"), ("Foley", "inst", "fx/foley"), ("Ambience", "inst", "fx/ambience"),
           ("Animals", "inst", "fx/animals"), ("Human", "inst", "fx/human"), ("Household", "inst", "fx/household"),
           ("Sci-fi", "inst", "fx/scifi"), ("Machines", "inst", "fx/machines"),
           ("Transport", "inst", "fx/transport"), ("Warfare", "inst", "fx/warfare")],
    "strings": [("Violin", "inst", "strings/violin"), ("Cello", "inst", "strings/cello"),
                ("Viola", "inst", "strings/viola"), ("Bass", "inst", "strings/bass"),
                ("Ensemble", "inst", "strings/ensemble"), ("Orchestral", "tag", "orchestral"),
                ("Synth", "tag", "synth"), ("Pads", "tag", "pad"), ("Staccato", "tag", "staccato"),
                ("Melody", "tag", "melody"), ("Harp", "inst", "strings/harp")],
    "vocal": [("Female", "tag", "female"), ("Male", "tag", "male"), ("Vocal FX", "inst", "vocal/fx"),
              ("Spoken", "inst", "vocal/spoken"), ("Vocoder", "tag", "vocoder"), ("Phrases", "inst", "vocal/phrase"),
              ("Screams", "tag", "scream"), ("Shouts", "inst", "vocal/adlib"), ("Whispers", "tag", "whisper"),
              ("Chops", "inst", "vocal/chop"), ("Choir", "inst", "vocal/choir")],
}
SHORT = {"brasswind": "Brass & WW", "percussion": "Percussion", "vocal": "Vocals",
         "drums/hat/closed": "Closed", "drums/hat/open": "Open", "drums/cymbal/ride": "Ride",
         "drums/cymbal/crash": "Crash", "fx/foley/concrete": "Concrete", "fx/ambience/outdoor": "Outdoor",
         "fx/ambience/city": "City", "fx/ambience/indoor": "Indoor", "fx/ambience/weather": "Weather"}

# Temporary generic classes from the name, refined from the audio into a subclass.
PARTIAL = {"drums/hat": ("drums/hat/closed", "drums/hat/open"),
           "drums/cymbal": ("drums/cymbal/ride", "drums/cymbal/crash")}

# old identifiers (korjaukset.json before the Splice structure) -> new ones
OLD = {"drums/closedhat": "drums/hat/closed", "drums/openhat": "drums/hat/open",
       "drums/ride": "drums/cymbal/ride", "drums/crash": "drums/cymbal/crash",
       "drums/perc": "percussion", "drums/perc/shaker": "percussion/shaker", "drums/perc/conga": "percussion/conga",
       "drums/perc/tambourine": "percussion/tambourine", "drums/perc/cowbell": "percussion/cowbell",
       "drums/perc/rim": "percussion/rim", "drums/perc/clave": "percussion/woodblock",
       "drums/perc/triangle": "percussion/triangle", "drums/perc/other": "percussion/other",
       "bass/808": "bass", "mallet": "keys/mallets", "woodwind": "brasswind", "brass": "brasswind",
       "strings/plucked": "guitar", "strings/plucked/acoustic": "guitar/acoustic",
       "strings/plucked/electric": "guitar/electric", "strings/plucked/world": "guitar/world",
       "strings/plucked/harp": "strings/harp", "strings/bowed": "strings", "plucked": "guitar",
       "plucked/acoustic": "guitar/acoustic", "plucked/electric": "guitar/electric", "plucked/world": "guitar/world",
       "plucked/harp": "strings/harp", "bowed": "strings",
       "fx/downlifter": "fx/downer", "fx/whoosh": "fx/sweep", "fx/texture": "fx/atmos"}


def migrate(node: str) -> str:
    return OLD.get(node, node)


def children(node: str) -> list[str]:
    if node in PARTIAL:
        return list(PARTIAL[node])
    d = node.count("/") + 1
    return [t for t in IDS if t.startswith(node + "/") and t.count("/") == d]


def label_path(node: str) -> str:
    """'percussion/shaker' -> 'Percussion › Shakers'."""
    parts = node.split("/")
    return " › ".join(LABEL.get("/".join(parts[:i + 1]), parts[i]) for i in range(len(parts)))


def leaf_label(node: str) -> str:
    return LABEL.get(node, node.split("/")[-1].capitalize())


def chip_label(node: str) -> str:
    return SHORT.get(node, leaf_label(node))


def matches(inst: str, node: str) -> bool:
    return inst == node or inst.startswith(node + "/")


# ---------------------------------------------------------------- name rules: instruments
_W = {
    "drums/kick": "kick kicks kik kck bassdrum bassdrums",
    "drums": "drum drums drm drumkit drumkits",         # generic: "Drum Hits", a hip hop "Drum Kit" (not a loop)
    "drums/snare": "snare snares snr crossstick snaredrum snaredrums",
    "drums/clap": "clap claps clp snap snaps fingersnap handclap handclaps",
    "drums/hat/closed": "closedhat closedhats chh chat",
    "drums/hat/open": "openhat openhats ohh ophat ohat",
    "drums/hat": "hat hats hihat hihats hh",
    "drums/cymbal/ride": "ride rides",
    "drums/cymbal/crash": "crash crashes china",
    "drums/cymbal": "cymbal cymbals cym",
    "drums/tom": "tom toms",
    "drums/loop": "drumloop drumloops fullloop fullloops fulldrum drumgroove",
    "drums/tops": "top tops toploop toploops nokick nokickloop nokickloops",
    "drums/break": "break breaks breakbeat breakbeats",
    "drums/fill": "fill fills drumfill",
    "percussion": "perc percs percussion percussions percussive",
    "percussion/shaker": "shaker shakers shake cabasa maracas",
    "percussion/conga": "conga congas cajon",
    "percussion/bongo": "bongo bongos",
    "percussion/tambourine": "tambourine tambourines tamb tambo",
    "percussion/cowbell": "cowbell cowbells cowb",
    "percussion/woodblock": "woodblock woodblocks clave claves",
    "percussion/bell": "agogo",
    "percussion/djembe": "djembe djembes",
    "percussion/timbale": "timbale timbales",
    "percussion/rim": "rim rims rimshot rimshots sidestick",
    "percussion/triangle": "triangle triangles",
    "percussion/tabla": "tabla tablas darbuka doumbek dhol handdrum handdrums",
    "bass": "bass basses bassline basslines 808s",      # "808s" by itself: weak, any drum word wins
    "bass/synth": "reese synthbass",
    "bass/sub": "sub subs subbass",
    "bass/electric": "bassguitar electricbass slapbass fretless",
    "bass/acoustic": "uprightbass acousticbass",
    "synth": "synth synths synthesizer",
    "synth/lead": "lead leads",
    "synth/pad": "pad pads",
    "synth/pluck": "pluck plucks",
    "synth/arp": "arp arps arpeggio arpeggios",
    "synth/stab": "stab stabs chordstab",
    "synth/brass": "synthbrass brasssynth",
    "synth/bell": "synthbell fmbell bellsynth",
    "synth/keys": "synthkeys",
    "brasswind": "brass winds woodwind woodwinds",
    "brasswind/sax": "sax saxophone saxes",
    "brasswind/trumpet": "trumpet trumpets",
    "brasswind/trombone": "trombone trombones",
    "brasswind/horn": "horn horns tuba flugelhorn frenchhorn",
    "brasswind/flute": "flute flutes panflute piccolo",
    "brasswind/clarinet": "clarinet oboe bassoon contrabassoon coranglais englishhorn",
    "brasswind/harmonica": "harmonica",
    "brasswind/whistle": "whistle whistles",
    "brasswind/world": "duduk shakuhachi shehnai didgeridoo recorder bansuri",
    "keys": "keys",
    "keys/piano": "piano pianos grandpiano",
    "keys/epiano": "rhodes epiano electricpiano",
    "keys/wurlitzer": "wurli wurlitzer",
    "keys/organ": "organ organs",
    "keys/clavinet": "clav clavinet",
    "keys/mallets": "bell bells marimba xylophone vibraphone vibes glockenspiel kalimba mallet mallets "
                    "celesta musicbox chime chimes",
    "guitar": "guitar guitars gtr",
    "guitar/acoustic": "acousticguitar nylonguitar classicalguitar",
    "guitar/electric": "electricguitar",
    "guitar/world": "sitar oud koto banjo ukulele mandolin bouzouki saz guzheng balalaika kora",
    "strings": "strings string orchestra",
    "strings/violin": "violin violins fiddle erhu",
    "strings/viola": "viola violas",
    "strings/cello": "cello cellos",
    "strings/bass": "doublebass contrabass",
    "strings/harp": "harp harps",
    "vocal": "vocal vocals vox voice voices acapella acapellas singer vowel vowels",
    "vocal/chop": "vocalchop vocalchops vocalslice vocalslices",
    "vocal/phrase": "phrase phrases verse hook hooks lyric lyrics sentence",
    "vocal/adlib": "adlib adlibs shout shouts hey yeah",
    "vocal/choir": "choir choirs chant chants",
    "vocal/spoken": "spoken speech dialogue dialog talking narration announcer",
    "vocal/fx": "vocalfx vocalatmos vocalpad vocaltexture reversevocal vocalreverse",
    "fx": "fx sfx",
    "fx/noise": "noise noises",
    "fx/riser": "riser risers uplifter uplifters rise build buildup",
    "fx/downer": "downlifter downlifters downer downers fall falls",
    "fx/impact": "impact impacts boom booms slam slams fxhit",
    "fx/sweep": "whoosh whooshes sweep sweeps swoosh transition transitions",
    "fx/atmos": "texture textures atmosphere atmospheres atmos drone drones",
    "fx/reverse": "reverse reversed",
    "fx/field": "fieldrecording fieldrecordings",
    "fx/glitch": "glitch glitches stutter",
    "fx/foley": "foley",
    "fx/foley/paper": "paper cardboard",
    "fx/foley/wood": "wood wooden",
    "fx/foley/plastic": "plastic",
    "fx/foley/metal": "metal metallic",
    "fx/foley/concrete": "concrete stone gravel",
    "fx/foley/fire": "fire",
    "fx/foley/water": "water drip",
    "fx/ambience": "ambience ambiance ambient roomtone",
    "fx/ambience/indoor": "indoor",
    "fx/ambience/outdoor": "outdoor nature forest",
    "fx/ambience/city": "city factory",       # not urban / street / industrial: in pack names those are styles
    "fx/ambience/weather": "weather rain thunder storm wind",
    "fx/animals": "animal animals bird birds dog dogs insect insects crow",
    "fx/human": "crowd footsteps footstep breath breathing heartbeat applause",
    "fx/household": "door doors kitchen bathroom cutlery dishes",
    "fx/scifi": "scifi laser lasers alien spaceship robot",
    "fx/machines": "machine machines phone typewriter",
    "fx/transport": "train trains aircraft airplane traffic",
    "fx/warfare": "gun guns gunshot knife sword military weapon bullet explosion",
}
KW = {w: node for node, ws in _W.items() for w in ws.split()}
# drum kit abbreviations at the start of a file name ("BD 2600 01", "OH Drumulator 17")
KIT_ABBR = {"bd": "drums/kick", "sd": "drums/snare", "cp": "drums/clap", "ch": "drums/hat/closed",
            "oh": "drums/hat/open", "hh": "drums/hat", "rs": "percussion/rim", "cb": "percussion/cowbell",
            "cy": "drums/cymbal", "lt": "drums/tom", "mt": "drums/tom", "ht": "drums/tom",
            "rim": "percussion/rim", "tamb": "percussion/tambourine", "sn": "drums/snare"}
# Descriptive words: they tell about the sound or its use, not the instrument. They decide the instrument only
# if nothing names an instrument ("Flute – Stab" = flute). As tags they are always kept.
WEAK = set("""bell bells chime chimes stab stabs chordstab pluck plucks lead leads pad pads arp arps
arpeggio arpeggios fx sfx top tops vibes atmosphere atmospheres atmos ambience ambiance ambient
texture textures drone drones noise noises fall falls rise build buildup shout shouts hey yeah sub subs
break breaks fill fills transition transitions sweep sweeps glitch glitches stutter impact
impacts boom booms slam slams whoosh whooshes swoosh riser risers phrase phrases hook hooks verse
sentence reverse reversed wood wooden metal metallic water fire paper stone wind orchestra
drum drums drm drumkit drumkits 808s percussive""".split())

KW.update({"celli": "strings/cello", "timpani": "percussion/other"})
# plurals nobody listed ("Bassoons", "Kalimbas", "Leads" is there already); not for the FX words, which are
# everyday words ("stones", "fires") and not for short abbreviations
for _w, _n in list(KW.items()):
    if _w.isalpha() and len(_w) >= 4 and _w[-1] != "s" and _w != "chat" and not _n.startswith("fx"):
        if KW.setdefault(_w + "s", _n) == _n and _w in WEAK:
            WEAK.add(_w + "s")

# ---------------------------------------------------------------- name rules: tags
_T = {
    "chords": "chord chords progression progressions chordloop chordloops chordprogression",
    "melody": "melody melodies motif motifs topline toplines melodyloop",
    "riff": "riff riffs",
    "lead": "lead leads",
    "stab": "stab stabs",
    "pad": "pad pads",
    "pluck": "pluck plucks",
    "arp": "arp arps arpeggio arpeggios",
    "staccato": "staccato stacc spiccato pizzicato pizz",
    "rhythm": "rhythm rhythmic strum strumming",
    "ensemble": "ensemble section sections",
    "orchestral": "orchestral orchestra",
    "classical": "classical",
    "synth": "synth synths",
    "analog": "analog analogue",
    "acid": "acid 303 tb303",
    "saw": "saw supersaw",
    "wobble": "wobble wob wobbles",
    "pulse": "pulse",
    "distorted": "distorted distortion fuzz overdrive overdriven",
    "clean": "clean",
    "acoustic": "acoustic",
    "808": "808 808s",
    "reverse": "reverse reversed",
    "female": "female fem",
    "male": "male",
    "vocoder": "vocoder talkbox",
    "scream": "scream screams screaming",
    "whisper": "whisper whispers whispered whispering",
}
TW = {w: tag for tag, ws in _T.items() for w in ws.split()}
# genres and other word pairs that are not instruments
_GENRE = re.compile(r"drum\s*(?:and|n|&|'n')\s*bass|\bdnb\b|bass\s*house|future\s*bass|bass\s*music|"
                    r"melodic\s*(?:house|techno)|tech\s*house|acid\s*house|acid\s*techno|"
                    r"\bno[\s_-]*(?:chords?|bass|lead|melody|drums?)\b", re.I)   # "no kick" = tops, not removed
_CHORD_SEQ = re.compile(r"\b[A-G][#b]?m?(?:aj7|7|9)?(?:\s*-\s*[A-G][#b]?m?(?:aj7|7|9)?){2,}\b")
_DRUM_PIECES = {"drums/kick", "drums/snare", "drums/clap", "drums/hat", "drums/hat/closed", "drums/hat/open",
                "drums/tom", "drums/cymbal", "drums/cymbal/ride", "drums/cymbal/crash"}


_GLUED_NUM = re.compile(r"^\d{0,4}([a-z]{3,})\d{0,3}$")   # "Kick2", "Open06", "36kick2", "808snare"
_GLUED_OK = {w for w in (*KW, *TW) if w.isalpha()} | {   # instrument and tag words + _pick's modifiers
    "open", "closed", "acoustic", "electric", "upright", "double", "reverse", "chop", "chops"}
_ABBR_NUM = re.compile(r"^([a-z]{2,4})\d{1,3}$")           # "oh07", "bd12": a kit abbreviation glued to its number
# hip hop kits write their plurals with a z: "Kickz", "Snarez", "Loopz"
_Z_PLURAL = {w for w in (*KW, *TW) if w.endswith("s")} | {"loops", "shots", "hits", "sounds"}


# an instrument written together with what it does: "SNAREROLL11", "guitarlicks", "KICKLOOP", "Padloop"
_TAILS = ("loops", "loop", "licks", "lick", "hits", "hit", "shots", "shot", "stabs", "stab", "riffs", "riff", "chords",
          "chord", "rolls", "roll", "fills", "fill", "samples", "sample", "sounds", "sound", "lines", "line", "phrases",
          "phrase")
_TAIL_RE = re.compile(r"^([a-z]{3,}?)(%s)(\d{0,3})$" % "|".join(_TAILS))


def _split(part: str) -> list[str]:
    """A name as lower-case words: "HiHat_Loopz-01" -> hi hat loops 01. Everything that is not a letter or a
    digit separates words ("808's" -> 808 s)."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", part)
    out = []
    for w in re.sub(r"[\W_]+", " ", s).lower().split():
        if len(w) > 3 and w[-1] == "z" and w[:-1] + "s" in _Z_PLURAL:
            w = w[:-1] + "s"
        m = _TAIL_RE.match(w) if len(w) > 5 and w not in KW and w not in TW else None
        if m and m.group(1) in KW:
            out += [x for x in m.groups() if x]
        else:
            out.append(w)
    return out


def _glued(t: list[str]) -> list[str]:
    """Known words glued to a number: "Open06", "Kick2", "36kick2" -> open, kick, kick."""
    out = []
    for w in t:
        m = _GLUED_NUM.match(w)
        if m and m.group(1) != w and m.group(1) in _GLUED_OK:
            out.append(m.group(1))
    return out


def _tokens(part: str) -> list[str]:
    t = _split(part)
    return t + [a + b for a, b in zip(t, t[1:])] + _glued(t)   # "drum loop" -> "drumloop", "synth brass" -> "synthbrass"


def _depth(node: str) -> int:
    return node.count("/")


def _pick(hits: list[str], toks: set[str], words: list[str]) -> tuple[str, set[str]]:
    """Instrument from the matches in one part of the name (+ tags that come from combinations)."""
    h = set(hits)
    if "drums/loop" in h or "drums/tops" in h:        # "kick loop" is a drum loop, "no kick loop" is tops
        if "drums/tops" in h:
            return "drums/tops", set()
        if not (h & {"percussion", "drums/hat", "bass", "synth"}) or "drumloop" in toks:
            return "drums/loop", set()
    drum = h & _DRUM_PIECES
    if "808" in toks and drum:                         # "808 Kick": drum + 808 tag
        return max(drum, key=hits.index), {"808"}
    if "synth" in h:                                   # synth + another instrument
        for other, node in (("brasswind/horn", "synth/brass"), ("brasswind", "synth/brass"),
                            ("brasswind/trumpet", "synth/brass"), ("keys/mallets", "synth/bell"),
                            ("keys", "synth/keys"), ("keys/piano", "synth/keys"), ("keys/organ", "synth/keys"),
                            ("bass", "bass/synth"), ("bass/sub", "bass/sub"), ("fx", "synth/fx")):
            if other in h:
                return node, {"synth"}
        for other in ("strings", "strings/violin", "strings/cello", "strings/ensemble", "guitar"):
            if other in h:                             # synth strings / synth guitar = instrument + synth tag
                return other, {"synth"}
        sub = [x for x in hits if x.startswith("synth/")]
        if sub:
            return sub[0], set()
    if "bass" in h and "guitar" in h:
        return "bass/electric", set()
    if "bass" in h and ({"upright", "acoustic"} & toks):
        return "bass/acoustic", set()
    if "bass" in h and "double" in toks:
        return "strings/bass", set()
    if "bass" in h and "electric" in toks:
        return "bass/electric", set()
    if "guitar" in h and "acoustic" in toks:
        return "guitar/acoustic", set()
    if "guitar" in h and "electric" in toks:
        return "guitar/electric", set()
    if "keys/piano" in h and "electric" in toks:
        return "keys/epiano", set()
    if "drums/hat" in h and "open" in toks:
        return "drums/hat/open", set()
    if "drums/hat" in h and "closed" in toks:
        return "drums/hat/closed", set()
    voc = [w for n, w in zip(hits, words) if n == "vocal" or n.startswith("vocal/")]
    others = [w for n, w in zip(hits, words) if not (n == "vocal" or n.startswith("vocal/")) and w not in WEAK]
    if voc and not (others and all(w in WEAK for w in voc)):      # "Synth Phrases" are not vocals
        if "reverse" in toks or h & {"fx", "fx/atmos"}:
            return "vocal/fx", set()
        sub = [x for x in hits if x.startswith("vocal/")]
        if sub:
            return sub[0], set()
        if "chop" in toks or "chops" in toks:
            return "vocal/chop", set()
        return "vocal", set()
    strong = [n for n, w in zip(hits, words) if w not in WEAK]
    if strong:                                         # an instrument word wins; the first of them
        best = strong[0]
        for n in strong[1:]:                           # "Perc Shaker" -> the more specific one in the same class
            if n.startswith(best + "/"):
                best = n
        return best, set()
    fxs = [x for x in hits if x.startswith("fx/")]
    if fxs:
        return max(fxs, key=_depth), set()
    return max(hits, key=lambda n: (_depth(n), -hits.index(n))), set()


# Part of a drum loop in the file name: "Drum Loop - Kick", "…_drag_top", "…_prc", "percussion_drum_loop_conga"
_LOOP_PART = {w: n for w, n in KW.items()
              if (n.startswith("drums/") and n not in ("drums/loop", "drums/break")) or n.startswith("percussion")}
_LOOP_PART.update({"prc": "percussion", "top": "drums/tops", "tops": "drums/tops"})
for _w in ("drumkit", "kitloop", "fulldrum", "drumloop", "drumloops", "fullloop", "fullloops", "nokick",
           "shake"):                                    # "full_loop_shake" etc.: not a part
    _LOOP_PART.pop(_w, None)


def loop_part(stem: str) -> str | None:
    """Refine a full drum loop from its name: one part (kick, snare, top, perc, conga …) -> its group.
    "full"/"ful" -> stays full; two drum parts ("kick snare combo") -> stays full;
    "no tabla" is not a part. Some packs: "Drum Loop - Kick - 131BPM - Title" – the title is not read."""
    stem = re.split(r"\d+\s*bpm\s*[-(]", stem, maxsplit=1, flags=re.I)[0]
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
    toks = re.sub(r"[_\-\.\(\)\[\]&,+#\s\d]+", " ", s).lower().split()
    if {"full", "ful", "fullloop"} & set(toks):
        return None
    parts = {_LOOP_PART[t] for k, t in enumerate(toks) if t in _LOOP_PART and (k == 0 or toks[k - 1] != "no")}
    if not parts:
        return None
    perc = {x for x in parts if x.startswith("percussion")}
    drum = parts - perc
    if "drums/tops" in drum:
        return "drums/tops"
    if "drums/fill" in drum:
        return "drums/fill"
    if "percussion" in perc:                            # the pack calls it percussion
        spec = perc - {"percussion"}
        return spec.pop() if len(spec) == 1 and not drum else "percussion"
    if len(parts) == 1:
        return parts.pop()
    return None                                         # kick + snare, conga + kick …: a full loop


_FOLDER_HINTS: dict[str, tuple] = {}


def _hint(part: str, stem: bool) -> tuple:
    """What one part of a path (stem = the file name, otherwise a folder) says:
    (instrument or None, strong = an instrument word rather than a descriptive one, tags, tags from word
    combinations, instrument for refining one found deeper, the instrument words)."""
    clean = _GENRE.sub(" ", re.sub(r"_+", " ", part))   # "_no_chord_": \b doesn't work with an underscore
    t = _split(clean)
    pairs = [a + b for a, b in zip(t, t[1:])]
    glued = _glued(t)
    toks = t + pairs + glued
    ts = set(toks)
    tags = {TW[w] for w in toks if w in TW}
    if stem and _CHORD_SEQ.search(clean):
        tags.add("chords")
    # a two-word name is one word: in "Bass Drum" there is no bass, in "Hand Drums" no drum kit
    paired = [w in KW for w in pairs]
    words = []                                          # in the order written: the first instrument word leads
    for k, w in enumerate(t):
        if k < len(pairs) and paired[k]:
            words.append(pairs[k])
        elif w in KW and not (k and paired[k - 1]):
            words.append(w)
    words += [w for w in glued if w in KW]
    if stem and not words and "808" in t:
        words = ["808s"]                                # "808 Shot 12 E": by itself an 808 is a bass
    refine = None
    if words and not all(w in WEAK for w in words):
        refine = _pick([KW[w] for w in words], ts, words)[0]
    if ts & {"ss", "songstarter", "songstarters"}:
        words = [w for w in words if w != "hh"]     # "HH SS - … - Piano" = Hip Hop songstarter, not a hi-hat
    hits = [KW[w] for w in words]
    if stem and t and (not hits or set(hits) <= {"bass"}) \
            and not tags & {"melody", "chords", "riff", "lead", "pad", "pluck", "arp"} and not ts & {"loop", "loops"}:
        # (not "SD_Melody_Trolly_80_B" or "SD_Loop_…": there the abbreviation is the vendor's)
        m = _ABBR_NUM.match(t[0])                   # "BD A 808 Decay" = kick, "CH Combo 808" = hat, "oh07" = open hat
        abbr = t[0] if t[0] in KIT_ABBR else m.group(1) if m and m.group(1) in KIT_ABBR else None
        if abbr:
            hits, words = [KIT_ABBR[abbr]], [abbr]
    elif not stem and not hits:
        alpha = [w for w in t if not w.isdigit()]   # a folder called "BD" or "909-SD"
        if len(alpha) == 1 and alpha[0] in KIT_ABBR:
            hits, words = [KIT_ABBR[alpha[0]]], alpha
    if not hits:
        return None, False, tags, frozenset(), refine, ()
    node, extra = _pick(hits, ts, words)
    return node, not all(w in WEAK for w in words), tags, frozenset(extra), refine, tuple(words)


_LIST = re.compile(r"\s*(?:[&,+]|\band\b)\s*", re.I)


def _folder_hint(name: str) -> tuple:
    """_hint for a folder name. A name that lists different things ("Claps & Snares", "Hi Hats, Crashes & Percs",
    "SYNTH & BASS") says only what they have in common: drums, drums, nothing."""
    h = _FOLDER_HINTS.get(name)
    if h is None:
        if len(_FOLDER_HINTS) > 200_000:
            _FOLDER_HINTS.clear()
        h = _hint(name, False)
        segs = [x for x in _LIST.split(_GENRE.sub(" ", re.sub(r"_+", " ", name))) if x.strip()]
        if len(segs) > 1:
            each = [_hint(x, False) for x in segs]
            nodes = [e[0] for e in each if e[0]]
            if len(set(nodes)) > 1:
                parts = [x.split("/") for x in nodes]
                k = 0
                while all(len(q) > k for q in parts) and len({q[k] for q in parts}) == 1:
                    k += 1
                common = "/".join(parts[0][:k]) or ("drums" if {q[0] for q in parts} <= {"drums", "percussion"} else None)
                h = (common, any(e[1] for e in each) and common is not None, h[2], frozenset(), None,
                     tuple(w for e in each for w in e[5]))
        _FOLDER_HINTS[name] = h
    return h


def _resolve(stem: str, folders: list[str], use: list[int] | None = None) -> tuple[str | None, set[str], int]:
    """The instrument from the file name, then the folders from the deepest upwards.
    use[k] for folders[k]: 2 = read as written, 1 = only the main class of its instrument word counts,
    0 = not read at all. -> (instrument, tags, level: 0 = the file name decided, k + 1 = folders[k], -1 = nothing)."""
    tags: set[str] = set()
    found = weak_found = None                           # weak = only a descriptive word so far
    level = weak_level = -1
    for k in range(len(folders) + 1):
        if k == 0:
            node, strong, tg, extra, refine = _hint(stem, True)[:5]
        else:
            u = 2 if use is None else use[k - 1]
            if not u:
                continue
            node, strong, tg, extra, refine = _folder_hint(folders[k - 1])[:5]
            if u == 1:
                node, refine = node and node.split("/")[0], None
        tags |= tg
        if found:
            # a folder can still refine the same instrument: file "Hat Loop" in "Open Hats" -> open hats
            if refine and refine.startswith(found + "/"):
                found = refine
            continue
        if node is None:
            continue
        tags |= extra
        if not strong:
            if weak_found is None:
                weak_found, weak_level = node, k        # wait and see whether a folder names the instrument
            continue
        found, level = node, k
        if weak_found and found.split("/")[0] == weak_found.split("/")[0] and _depth(weak_found) > _depth(found):
            found = weak_found                          # "Synth/…Pad" -> synth/pad
    inst = found or weak_found
    if inst == "drums/loop":
        inst = loop_part(stem) or inst
    if inst:
        if inst.startswith("synth"):
            tags.discard("synth")                       # synth tag only for other instruments
        if "808" in tags and not inst.startswith(("bass", "drums")):
            tags.discard("808")
        main = inst.split("/")[0]
        if main in ("drums", "percussion", "fx"):       # on drums "lead"/"pad" etc. mean something else
            tags -= {"lead", "pad", "pluck", "arp"}
    return inst, tags, level if found else weak_level


def from_name(path: str, root: str) -> tuple[str | None, set[str]]:
    """-> (instrument or None, tags) of one file by itself. root = the library's root folder; the first folder
    under it (the pack) is not read. For a whole library use name_labels, which knows where the packs are."""
    rel = path[len(root):].lstrip("/").split("/")
    return _resolve(rel[-1].rsplit(".", 1)[0], rel[1:-1][::-1])[:2]


# ---------------------------------------------------------------- names in a whole library
# Folders inside a pack describe their content ("Kicks", "Bass Loops") and are read as written. Names of packs
# and of the folders above them are titles: "Dark Bass 2", "Techno Machine", "Hybrid Trap & Bass" say nothing
# about a file below them, "Soulful House Vocals", "Latin Pop Guitars", "Dr. Dre Drum Kit" do. Two checks:
# the files below that name their own instrument (a title most of them contradict is ignored), and the sound
# of the file itself (a title's instrument is used only when the sound-alikes don't contradict it, classify:
# a chord loop in "Techno Drums 3" is not a drum).
N_EVIDENCE = 5        # files below a title that name their own instrument, before they can rule it out
TITLE_DENY = 0.4      # ignored: fewer than this share of them is of the title's main class
TITLE_AUDIO = 0.5     # used for a file when this share of its sound-alike votes is for the title's main class


def _same_main(a: str, b: str) -> bool:
    x, y = a.split("/")[0], b.split("/")[0]
    return x == y or {x, y} == {"drums", "percussion"}      # packs draw that line in different places


def name_labels(paths: list[str], roots=None, words: list | None = None):
    """from_name for a whole library -> ([(instrument or None, tags)], source, alternatives).
    source: what decided, "file" / "folder" / "". alternatives: per file None, or the (instrument, tags)
    it gets from a title if its sound agrees (TITLE_AUDIO).
    words: a list to fill with the instrument words that decided, per file (testit/tarkkuus.py)."""
    import pakkaukset
    import polut
    rp = polut.roots_per_path(paths, roots)
    _, ntitle = pakkaukset.find(paths, roots)
    stems, chains = [], []                              # per file: name; its folders as paths, deepest first
    for p, r in zip(paths, rp):
        cuts = [k for k in range(len(r) + 1, len(p)) if p[k] == "/"]
        stems.append(p[(cuts[-1] if cuts else len(r)) + 1:].rsplit(".", 1)[0])
        chains.append([p[:k] for k in reversed(cuts)])
    name_of = lambda f: f[f.rfind("/") + 1:]            # noqa: E731
    # 1. evidence for every title with an instrument word: what the files below it say themselves
    ev: dict[str, list[int]] = {}
    for stem, chain, nt in zip(stems, chains, ntitle):
        node, strong = _hint(stem, True)[:2]
        label = node if strong else None
        depth = len(chain)
        for k, f in enumerate(chain):
            node, strong = _folder_hint(name_of(f))[:2]
            if node is None:
                continue
            if depth - k > nt:                          # a folder inside the pack: it names the file's instrument
                if label is None and strong:
                    label = node
                continue
            if label is not None:
                e = ev.setdefault(f, [0, 0])
                e[0] += 1
                e[1] += _same_main(label, node)
    # 2. which folders are read: 2 = yes, 0 = no, 3 = a title to be tried against the sound
    usage: dict[str, int] = {}

    def title_use(f: str) -> int:
        u = usage.get(f)
        if u is None:
            node, strong = _folder_hint(name_of(f))[:2]
            if node is None or (not strong and node.startswith("fx")):
                u = 0                                   # nothing, or an everyday word ("Ambient", "Noise", "Fire")
            else:
                n, agree = ev.get(f, (0, 0))
                u = 0 if n >= N_EVIDENCE and agree < TITLE_DENY * n else 3
            usage[f] = u
        return u

    out, src, alt = [], [], []
    for stem, chain, nt in zip(stems, chains, ntitle):
        depth = len(chain)
        use = [title_use(f) if depth - k <= nt else 2 for k, f in enumerate(chain)]
        names = [name_of(f) for f in chain]
        inst, tags, level = _resolve(stem, names, [u if u != 3 else 0 for u in use])
        out.append((inst, tags))
        src.append("" if level < 0 else "file" if level == 0 else "title" if depth - (level - 1) <= nt else "folder")
        if words is not None:
            words.append(() if level < 0 else _hint(stem, True)[5] if level == 0 else _folder_hint(names[level - 1])[5])
        other = None
        if 3 in use:
            i2, t2, _ = _resolve(stem, names, [2 if u == 3 else u for u in use])
            if i2 != inst:
                other = (i2, t2)
        alt.append(other)
    return out, src, alt


# ---------------------------------------------------------------- stem families
# "Artist_99_soulful_loop_hugger_bounce_" + full / bass_pads / chords: stems of the same loop in the same folder.
# The common start is a title; only the end tells the instrument. Of the descriptive words only clear stem names
# count ("Stadium Boom", "slam" are titles).
_STEM_WEAK = set("pad pads pluck plucks lead leads arp arps arpeggio arpeggios stab stabs top tops fx sfx break breaks fill fills "
                 "drums".split())
_MELODIC = ("synth", "keys", "guitar", "strings", "brasswind")


def _ftoks(stem: str) -> list[str]:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
    return re.sub(r"[_\-\.\(\)\[\]&,+#\s]+", " ", s).lower().split()


def stem_node(suffix: list[str]) -> str | None:
    """Instrument from a stem's ending: "bass pads amaj" -> synth/pad, "synth bass" -> bass/synth,
    "full" -> None (the whole mix). "no bass" is not a bass."""
    t = [w for k, w in enumerate(suffix) if w != "no" and not (k and suffix[k - 1] == "no")]
    if {"full", "ful", "fullloop", "mix", "master"} & set(t):
        return None
    toks = _tokens(" ".join(t))
    words = [w for w in toks if w in KW and (w not in WEAK or w in _STEM_WEAK)]
    hits = [KW[w] for w in words]
    if not hits:
        return None
    rest = [(h, w) for h, w in zip(hits, words) if not h.startswith("bass")]
    if rest and len(rest) < len(hits) and all("/" in h and h.split("/")[0] in _MELODIC for h, _ in rest):
        hits, words = [h for h, _ in rest], [w for _, w in rest]     # "bass pads" -> pads (the bass is accompaniment)
    return _pick(hits, set(toks), words)[0]


def stem_families(paths: list[str]) -> dict[int, str]:
    """-> {index: instrument} for stems whose ending names an instrument. Family = same folder and
    a common start of at least 3 words (neighbours in the sorted list) with no instrument word
    ("Flute Loop 01/02" is a series, not stems) and no "no" at the end ("…_no_bass" = a mix without bass)."""
    import os
    byf: dict[str, list[int]] = {}
    for i, p in enumerate(paths):
        byf.setdefault(os.path.dirname(p), []).append(i)
    out = {}
    for ids in byf.values():
        if len(ids) < 2:
            continue
        T = sorted((_ftoks(os.path.basename(paths[i]).rsplit(".", 1)[0]), i) for i in ids)
        lcp = lambda a, b: next((n for n, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))  # noqa: E731
        for j, (t, i) in enumerate(T):
            n = max(lcp(t, T[j - 1][0]) if j else 0, lcp(t, T[j + 1][0]) if j + 1 < len(T) else 0)
            if n < 3 or n >= len(t) or t[n - 1] == "no":
                continue
            if any(w in KW for w in _tokens(" ".join(t[:n]))):
                continue
            node = stem_node(t[n:])
            if node:
                out[i] = node
    return out


# ---------------------------------------------------------------- audio: nearest neighbours
# Generic classes from the name that the sound may refine, when it is clear enough: a hip hop "Drum Kit" folder
# of unnamed hits -> Kicks, Snares … (a share of all sound-alike votes; PARTIAL classes are always refined).
REFINE = {"drums": 0.6}


def classify(paths: list[str], A, k: int = 15, chunk: int = 500, fixed: dict | None = None, roots=None):
    """-> (instrument array (str, '' = unknown), estimated array (bool), tags (list[set])).
    roots = the index's library roots (polut.roots_per_path)."""
    import numpy as np
    named, _, alt = name_labels(paths, roots)
    inst = np.array([n or "" for n, _ in named], dtype=object)
    tags = [t for _, t in named]
    for i, node in stem_families(paths).items():     # a stem's ending beats the folder and the title
        if not (inst[i] == node or inst[i].startswith(node + "/")):
            inst[i] = node
            alt[i] = None
    for i, node in (fixed or {}).items():              # user corrections = certain examples
        inst[i] = node
        alt[i] = None
    est = np.zeros(len(paths), dtype=bool)
    maybe = np.array([a is not None for a in alt])          # a title the sound has to confirm (name_labels)

    # reliable examples: named, and not waiting for the sound to say more
    leafish = np.array([bool(n) and n not in PARTIAL and n not in REFINE for n in inst]) & ~maybe
    L = np.nonzero(leafish)[0]
    user = np.zeros(len(paths), dtype=bool)                 # user corrections stay as they are,
    user[list((fixed or {}).keys())] = True                 # also at the generic level ("Hats" without Closed/Open)
    classes = sorted(set(inst[L]))
    cid = {c: j for j, c in enumerate(classes)}
    yL = np.array([cid[c] for c in inst[L]])
    freq = np.bincount(yL, minlength=len(classes)).astype(np.float32)
    wcls = 1 / np.sqrt(np.maximum(freq, 1))            # small classes don't drown under big ones
    mains = np.array([c.split("/")[0] for c in classes])
    todo = np.nonzero(~leafish & ~user)[0]
    allowed = {}
    for c in set(inst[todo]):
        if not c:
            continue
        kids = PARTIAL.get(c) or [x for x in classes if x.startswith(c + "/")]
        allowed[c] = np.array([cid[x] for x in kids if x in cid], dtype=int)
    AL = A[L].astype(np.float32)
    for s in range(0, len(todo), chunk):
        q = todo[s:s + chunk]
        votes = knn_votes(A[q], AL, yL, wcls, len(classes), k)
        for r, i in enumerate(q):
            v = votes[r]
            tot = v.sum()
            if maybe[i]:                               # the title's instrument, unless the sound says otherwise
                node, tg = alt[i]
                m = node.split("/")[0]
                same = (mains == m) | ((mains == "percussion") if m == "drums" else (mains == "drums") if m == "percussion" else False)
                if tot > 0 and v[same].sum() >= TITLE_AUDIO * tot:
                    inst[i], tags[i] = node, tg
            c = inst[i]
            if c in REFINE or c in PARTIAL:            # the name gave a generic class: refine it
                al = allowed.get(c)
                if al is None or not len(al) or v[al].sum() <= 0:
                    if c in PARTIAL:
                        inst[i] = PARTIAL[c][0]
                        est[i] = True
                    continue
                best = al[np.argmax(v[al])]
                if c in PARTIAL or v[best] >= REFINE[c] * tot:
                    inst[i] = classes[best]
                    est[i] = True
            elif not c:                                # no name: a vote, confident ones only
                node = vote_pick(v, classes)
                if node:
                    inst[i], est[i] = node, True
    return inst, est, tags


def knn_votes(Aq, AL, yL, wcls, n_classes: int, k: int = 15):
    """Audio neighbour votes: rows of Aq against the labelled examples AL (labels yL, class weights wcls)."""
    import numpy as np
    sim = Aq.astype(np.float32) @ AL.T
    k = min(k, AL.shape[0] - 1)
    nn = np.argpartition(-sim, k, axis=1)[:, :k]
    votes = np.zeros((len(Aq), n_classes), dtype=np.float32)
    rows = np.arange(len(Aq))
    for j in range(k):
        yy = yL[nn[:, j]]
        np.add.at(votes, (rows, yy), np.exp(sim[rows, nn[:, j]] * 10) * wcls[yy])
    return votes


# Measured (testit/soitin_kehitys.py, pack series held out): 0.45 / 0.60 answered 87.5 % at 82 % main class right;
# chosen: fewer but surer guesses -> 64 % answered, 89 % right. Other audio models didn't do better.
VOTE_LEAF = 0.65      # share of the votes a subclass needs (no name)
VOTE_MAIN = 0.85      # otherwise the main class's share


def vote_pick(v, classes: list[str]) -> str:
    """No name: the voted class, the main class if only that is clear, '' if uncertain."""
    import numpy as np
    tot = v.sum()
    if tot <= 0:
        return ""
    best = int(np.argmax(v))
    if v[best] / tot >= VOTE_LEAF:
        return classes[best]
    mains = {}                                   # uncertain subclass: is the main class good enough?
    for j, c2 in enumerate(classes):
        mains[c2.split("/")[0]] = mains.get(c2.split("/")[0], 0) + v[j]
    m = max(mains, key=mains.get)
    return m if mains[m] / tot >= VOTE_MAIN else ""


# ---------------------------------------------------------------- style tags from the audio
# Measured leaving one pack out at a time: only these reach ≥ 85 % precision.
# Clean, Acoustic, Analog, Acid, Wobble and Screams can't be told apart reliably from audio -> name only.
#   tag: (scope, counter-tag (for comparison) or None, threshold)
SOUND_TAGS = {
    "distorted": (("guitar", "bass", "synth", "drums"), "clean", 0.90),   # precision 87 %
    "reverse": (("fx", "vocal", "synth", "bass", "keys", "guitar"), None, 0.90),   # 87 % (0.8); tightened
    "whisper": (("vocal",), None, 0.60),                                    # 91 %
    "vocoder": (("vocal",), None, 0.80),                                    # 86 % (0.7); tightened
    # 808: 85 % in the measurement, but the sample showed deep basses (Juno, dub bass) -> name only
}
GENDER_TH = 0.75     # female/male: 85–88 % already at a threshold of 0.5–0.66; stricter, because not all vocals are solo


def _logreg(X, y, iters=200, lr=2.0, l2=1e-3):
    import numpy as np
    w = np.where(y == 1, 0.5 / y.mean(), 0.5 / (1 - y.mean()))
    b, c = np.zeros(X.shape[1]), 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ b + c)))
        g = (p - y) * w
        b -= lr * (X.T @ g / w.sum() + l2 * b)
        c -= lr * g.sum() / w.sum()
    return b, c


def estimate_tags(inst, tags: list[set], A) -> list[set]:
    """Character and sound tags from the audio for samples whose name has no such tag. -> estimated tags / sample."""
    import numpy as np
    n = len(tags)
    main = np.array([x.split("/")[0] for x in inst])
    has = lambda t: np.array([t in s for s in tags])          # noqa: E731
    out = [set() for _ in range(n)]
    X = A                                    # rows converted per use: a float64 copy of all was ~0.9 GB
    rng = np.random.default_rng(0)
    for t, (scope, anti, th) in SOUND_TAGS.items():
        insc = np.isin(main, scope)
        pos = insc & has(t)
        if pos.sum() < 20:
            continue
        neg = insc & (has(anti) if anti else ~has(t))
        if anti is None:                                       # for comparison, a sample of unnamed ones
            cand = np.nonzero(neg)[0]
            neg = np.zeros(n, bool)
            neg[rng.choice(cand, min(len(cand), 6 * int(pos.sum())), replace=False)] = True
        L = np.nonzero(pos | neg)[0]
        b, c = _logreg(np.asarray(X[L], dtype=np.float64), pos[L].astype(float))
        todo = np.nonzero(insc & ~has(t) & ~(has(anti) if anti else np.zeros(n, bool)))[0]
        p = 1 / (1 + np.exp(-(np.asarray(X[todo], dtype=np.float64) @ b + c)))
        for i in todo[p >= th]:
            out[i].add(t)
    # gender: female vs. male vocals, only clear cases
    voc = main == "vocal"
    f, m = voc & has("female"), voc & has("male")
    if f.sum() >= 50 and m.sum() >= 50:
        L = np.nonzero(f | m)[0]
        b, c = _logreg(np.asarray(X[L], dtype=np.float64), f[L].astype(float))
        todo = np.nonzero(voc & ~f & ~m)[0]
        p = 1 / (1 + np.exp(-(np.asarray(X[todo], dtype=np.float64) @ b + c)))
        for i, pi in zip(todo, p):
            if pi >= GENDER_TH:
                out[i].add("female")
            elif pi <= 1 - GENDER_TH:
                out[i].add("male")
    return out
