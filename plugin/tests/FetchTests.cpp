// Offline tests for the Fetch plugin: key math, tempo plans, rendering (stretch + transpose)
// and the processor's host-synced playback with a fake play head.
//   cmake --build build --target FetchTests && build/FetchTests_artefacts/Release/FetchTests
// With FETCH_TEST_SERVICE=1 also asks the running search service for a real loop and renders it.
#include "../Source/PluginProcessor.h"
#include "../Source/TagEditor.h"
#include "../Source/Prefs.h"
#include "../Source/Sidebar.h"
#include <algorithm>
#include <cmath>
#include <iostream>

static int failures = 0;
#define CHECK(cond, msg)                                                         \
    do {                                                                         \
        if (cond) std::cout << "  ok    " << msg << "\n";                        \
        else { std::cout << "  FAIL  " << msg << "  (" #cond ")\n"; ++failures; } \
    } while (0)

static juce::File tmpDir()
{
    auto d = juce::File::getSpecialLocation (juce::File::tempDirectory).getChildFile ("fetch-plugin-tests");
    d.createDirectory();
    return d;
}

// A loop of short tone bursts (20 ms, 1 kHz, like a drum hit) on every beat at `bpm`, `beats` long.
static juce::File writeClickLoop (double bpm, int beats, double sr, const juce::String& name)
{
    const int spb = (int) std::lround (60.0 / bpm * sr);
    juce::AudioBuffer<float> b (1, spb * beats);
    b.clear();
    for (int k = 0; k < beats; ++k)
        for (int i = 0; i < (int) (0.02 * sr); ++i)
            b.setSample (0, k * spb + i, 0.8f * (float) std::sin (2 * juce::MathConstants<double>::pi * 1000 * i / sr)
                                             * (1.0f - (float) i / (float) (0.02 * sr)));
    auto f = tmpDir().getChildFile (name);
    f.deleteFile();
    juce::WavAudioFormat wav;
    std::unique_ptr<juce::OutputStream> os (f.createOutputStream());
    auto w = wav.createWriterFor (os, juce::AudioFormatWriterOptions().withSampleRate (sr).withNumChannels (1).withBitsPerSample (24));
    w->writeFromAudioSampleBuffer (b, 0, b.getNumSamples());
    return f;
}

static juce::File writeSine (double hz, double secs, double sr, const juce::String& name)
{
    juce::AudioBuffer<float> b (2, (int) (secs * sr));
    for (int i = 0; i < b.getNumSamples(); ++i)
        for (int c = 0; c < 2; ++c)
            b.setSample (c, i, 0.5f * (float) std::sin (2 * juce::MathConstants<double>::pi * hz * i / sr));
    auto f = tmpDir().getChildFile (name);
    f.deleteFile();
    juce::WavAudioFormat wav;
    std::unique_ptr<juce::OutputStream> os (f.createOutputStream());
    auto w = wav.createWriterFor (os, juce::AudioFormatWriterOptions().withSampleRate (sr).withNumChannels (2).withBitsPerSample (24));
    w->writeFromAudioSampleBuffer (b, 0, b.getNumSamples());
    return f;
}

// Frequency from zero crossings over the middle of a channel.
static double frequency (const float* x, int n, double sr)
{
    int crossings = 0, first = -1, last = -1;
    for (int i = n / 4 + 1; i < 3 * n / 4; ++i)
        if (x[i - 1] < 0 && x[i] >= 0)
        {
            if (first < 0) first = i;
            last = i;
            ++crossings;
        }
    return crossings > 1 ? (crossings - 1) * sr / (last - first) : 0.0;
}

// Sample indices where the envelope rises over 40 % of its maximum (burst onsets).
static std::vector<int> onsets (const float* x, int n, float = 0)
{
    std::vector<float> env ((size_t) n);
    float e = 0, peak = 0;
    for (int i = 0; i < n; ++i)
    {
        e = std::max (std::abs (x[i]), e * 0.995f);
        env[(size_t) i] = e;
        peak = std::max (peak, e);
    }
    std::vector<int> out;
    bool above = false;
    for (int i = 0; i < n; ++i)
    {
        if (! above && env[(size_t) i] > 0.4f * peak)
        {
            out.push_back (i);
            above = true;
        }
        else if (above && env[(size_t) i] < 0.2f * peak)
            above = false;
    }
    return out;
}

static SampleInfo info (const juce::File& f, const juce::String& type, double bpm, double dur, const juce::String& key = {})
{
    SampleInfo s;
    s.path = f.getFullPathName();
    s.name = f.getFileName();
    s.type = type;
    s.bpm = bpm;
    s.dur = dur;
    s.key = key;
    return s;
}

static void testKeys()
{
    std::cout << "keys\n";
    CHECK (keys::semitonesBetween ("C", "D") == 2, "C -> D = +2");
    CHECK (keys::semitonesBetween ("Am", "C") == 0, "Am in C major = 0 (relative)");
    CHECK (keys::semitonesBetween ("C", "Am") == 0, "C in A minor = 0 (relative)");
    CHECK (keys::semitonesBetween ("Em", "Am") == 5, "Em -> Am = +5");
    CHECK (keys::semitonesBetween ("Bm", "Cm") == 1, "Bm -> Cm = +1");
    CHECK (keys::semitonesBetween ("F#", "C") == -6, "F# -> C = -6 (never more than 6)");
    CHECK (keys::semitonesBetween ("", "C") == 0, "unknown key: no shift");
    CHECK (keys::semitonesBetween ("Bb", "A#") == 0, "enharmonic");
    CHECK (keys::parse ("C#m").first == 1 && keys::parse ("C#m").second, "parse C#m");
    CHECK (keys::all().size() == 24, "24 keys");
}

static void testPlans()
{
    std::cout << "tempo plans\n";
    juce::File f ("/x.wav");
    auto p = planFor (info (f, "Loop", 100, 2.4, "Am"), 120, 48000, true, "C");
    CHECK (p.tempo && std::abs (p.beats - 4) < 1e-9 && std::abs (p.sampleBpm - 100) < 1e-9, "100 BPM loop of 2.4 s = 4 beats");
    CHECK (p.semitones == 0, "Am loop in C: no shift");
    p = planFor (info (f, "Loop", 70, 6.857142, ""), 140, 48000, true, "");
    CHECK (std::abs (p.sampleBpm - 140) < 1e-9 && std::abs (p.beats - 16) < 1e-6, "70 BPM loop in 140 project counted at 140 (16 beats)");
    p = planFor (info (f, "Loop", 174, 5.517, ""), 87, 48000, true, "");
    CHECK (std::abs (p.sampleBpm - 87) < 1e-9, "174 BPM loop in 87 project counted at 87");
    p = planFor (info (f, "One-shot", 0, 0.5, "D"), 120, 48000, true, "E");
    CHECK (! p.tempo && p.semitones == 2, "one-shot: no stretch, key shift +2");
    p = planFor (info (f, "Loop", 100, 2.4, ""), 120, 48000, false, "");
    CHECK (! p.changesAudio(), "sync off: untouched");
    CHECK (planFor (info (f, "Loop", 100, 2.4, ""), 128, 48000, true, "").suffix() == "128 BPM", "file name suffix");
    // half / double only when the tempos are really about 2:1
    auto counted = [&] (double bpm, double host, double mult = 0) {
        auto s = info (f, "Loop", bpm, 16 * 60 / bpm, "");
        s.lenMult = mult;
        return planFor (s, host, 48000, true, "").sampleBpm;
    };
    CHECK (std::abs (counted (174, 120) - 174) < 1e-9, "174 break in a 120 project: slowed beat for beat, not double-time 87");
    CHECK (std::abs (counted (174, 100) - 87) < 1e-9, "174 in 100: counted at 87");
    CHECK (std::abs (counted (105, 174) - 210) < 1e-9, "105 in 174: counted at 210");
    CHECK (std::abs (counted (120, 174) - 120) < 1e-9, "120 in 174: beat for beat");
    CHECK (std::abs (counted (174, 120, 0.5) - 87) < 1e-9, "len /2: counted at 87 whatever the host");
    CHECK (std::abs (counted (90, 120, 2) - 180) < 1e-9, "len x2: counted at 180");
    {
        auto p2 = planFor ([&] { auto s = info (f, "Loop", 174, 16 * 60 / 174.0, ""); s.lenMult = 0.5; return s; }(), 120, 48000, true, "");
        CHECK (std::abs (p2.beats - 8) < 1e-6, "len /2: a 16-beat loop is 8 beats");
    }
}

static void testDragItem()
{
    std::cout << "drag items\n";
    const juce::File f (juce::String::fromUTF8 ("/mnt/x share/Sample Collection/Kick #1 100% \xc3\xa4.wav"));
    const auto u = dragItem (f);
   #if JUCE_LINUX
    CHECK (u.startsWith ("file:///") && ! u.containsChar (' '), "file:// URI without spaces: " + u.toStdString());
    CHECK (juce::URL (u).getLocalFile() == f, "decodes back to the same path");
   #else
    CHECK (u == f.getFullPathName(), "plain path");
   #endif
}

static void testRender()
{
    std::cout << "rendering\n";
    Renderer r;
    juce::String err;
    // 100 BPM, 4 beats at 44.1 kHz -> 120 BPM at 48 kHz: exactly 2 s = 96000 samples, clicks every 24000
    auto f = writeClickLoop (100, 4, 44100, "clicks100.wav");
    auto s = info (f, "Loop", 100, 2.4);
    auto plan = planFor (s, 120, 48000, true, "");
    auto p = r.render (s, plan, err);
    CHECK (p != nullptr, "render ok " + err);
    if (p == nullptr)
        return;
    CHECK (p->audio.getNumSamples() == 96000, "length " + juce::String (p->audio.getNumSamples()) + " == 96000");
    CHECK (p->synced && p->loop, "synced loop");
    auto on = onsets (p->audio.getReadPointer (0), p->audio.getNumSamples(), 0.15f);
    // a click right before the loop point is the downbeat running across it (seamless loop)
    on.erase (std::remove_if (on.begin(), on.end(), [&] (int o) { return o > p->audio.getNumSamples() - 480; }), on.end());
    bool grid = on.size() == 4;
    for (size_t k = 0; k < on.size() && grid; ++k)
        grid = std::abs (on[k] - (int) k * 24000) < 480;   // within 10 ms of the beat
    juce::String where;
    for (auto o : on) where << o << " ";
    CHECK (grid, "clicks on the 120 BPM beat grid: " + where);

    CHECK (p->complete(), "complete when returned");

    // Browsing loops: a long loop can play after its first chunk while the rest renders
    {
        auto lf = writeClickLoop (100, 64, 44100, "clicks100long.wav");   // 38 s
        auto ls = info (lf, "Loop", 100, 38.4);
        int readyAtStart = -1;
        double firstMs = 0;
        const auto t0 = juce::Time::getMillisecondCounterHiRes();
        auto lp = r.render (ls, planFor (ls, 120, 48000, true, ""), err, {}, [&] (std::shared_ptr<Preview> early) {
            readyAtStart = early->ready.load();
            firstMs = juce::Time::getMillisecondCounterHiRes() - t0;
        });
        const double allMs = juce::Time::getMillisecondCounterHiRes() - t0;
        CHECK (lp != nullptr && readyAtStart > 0 && readyAtStart < lp->audio.getNumSamples(),
               "playable after the first chunk (" + juce::String (readyAtStart) + " frames)");
        CHECK (firstMs < allMs / 4, "first sound after " + juce::String (firstMs, 1) + " ms of " + juce::String (allMs, 0) + " ms");
        if (lp != nullptr)
        {
            CHECK (lp->complete() && lp->audio.getNumSamples() == 64 * 24000, "then complete, exact length");
            auto lon = onsets (lp->audio.getReadPointer (0), lp->audio.getNumSamples(), 0.15f);
            lon.erase (std::remove_if (lon.begin(), lon.end(), [&] (int o) { return o > lp->audio.getNumSamples() - 480; }), lon.end());
            bool ok = lon.size() == 64;
            for (size_t k = 0; k < lon.size() && ok; ++k)
                ok = std::abs (lon[k] - (int) k * 24000) < 480;
            CHECK (ok, "all 64 beats on the grid (" + juce::String ((int) lon.size()) + " onsets)");
        }
    }

    auto out = Renderer::writeWav (*p, err);
    CHECK (out.existsAsFile() && out.getFileName().contains ("120 BPM"), "rendered WAV " + out.getFileName());
    if (juce::SystemStats::getEnvironmentVariable ("FETCH_KEEP", {}).isEmpty()) out.deleteFile();

    // transpose: 440 Hz in D -> project E = +2 semitones = 493.9 Hz
    auto sf = writeSine (440, 1.0, 48000, "sine.wav");
    auto si = info (sf, "One-shot", 0, 1.0, "D");
    auto p2 = r.render (si, planFor (si, 120, 48000, true, "E"), err);
    CHECK (p2 != nullptr && p2->audio.getNumSamples() == 48000, "one-shot keeps its length");
    if (p2)
    {
        const double hz = frequency (p2->audio.getReadPointer (0), p2->audio.getNumSamples(), 48000);
        CHECK (std::abs (hz - 493.88) < 3, "transposed to " + juce::String (hz, 1) + " Hz (493.9)");
    }
    // resampling only: 44.1k sine played at 48k keeps its pitch
    auto sf2 = writeSine (440, 1.0, 44100, "sine441.wav");
    auto p3 = r.render (info (sf2, "One-shot", 0, 1.0), planFor (info (sf2, "One-shot", 0, 1.0), 120, 48000, true, ""), err);
    CHECK (p3 != nullptr && std::abs (frequency (p3->audio.getReadPointer (0), p3->audio.getNumSamples(), 48000) - 440) < 2,
           "44.1 kHz file at 48 kHz keeps 440 Hz");
}

// A play head we move by hand.
struct FakePlayHead : juce::AudioPlayHead
{
    double bpm = 120, ppq = 0;
    bool playing = false;
    juce::Optional<PositionInfo> getPosition() const override
    {
        PositionInfo i;
        i.setBpm (bpm);
        i.setPpqPosition (ppq);
        i.setIsPlaying (playing);
        return i;
    }
};

static void testHostSync()
{
    std::cout << "host sync (processor)\n";
    const double sr = 48000;
    const int block = 480;
    FetchProcessor proc;
    FakePlayHead head;
    proc.setPlayHead (&head);
    proc.setRateAndBufferSizeDetails (sr, block);
    proc.prepareToPlay (sr, block);
    proc.settings.setProperty ("sync", true, nullptr);

    juce::AudioBuffer<float> buf (2, block);
    juce::MidiBuffer midi;
    auto process = [&] (int blocks, std::vector<float>* rec) {
        for (int b = 0; b < blocks; ++b)
        {
            proc.processBlock (buf, midi);
            if (rec)
                rec->insert (rec->end(), buf.getReadPointer (0), buf.getReadPointer (0) + block);
            if (head.playing)
                head.ppq += head.bpm / 60.0 * block / sr;
        }
    };
    auto pump = [] (int ms) { juce::MessageManager::getInstance()->runDispatchLoopUntil (ms); };

    process (2, nullptr);   // learn the host tempo
    auto f = writeClickLoop (100, 4, 48000, "clicks100_48.wav");
    // Transport running, somewhere in the middle of bar 3 (ppq 9.5): the loop must join in phase.
    head.playing = true;
    head.ppq = 9.5;
    proc.play (info (f, "Loop", 100, 2.4));
    for (int t = 0; t < 50 && ! proc.isPreviewing(); ++t)
    {
        pump (20);
        process (1, nullptr);
    }
    CHECK (proc.isPreviewing(), "preview started");
    // the loop renders in chunks in the background; this test runs faster than real time, so let the
    // render finish first (a slow CI machine otherwise plays the not-yet-rendered start as silence)
    for (int t = 0; t < 500 && ! proc.previewComplete(); ++t)
    {
        pump (20);
        process (1, nullptr);
    }
    CHECK (proc.previewComplete(), "preview rendered");
    const double startPpq = head.ppq;
    std::vector<float> rec;
    process (200, &rec);   // 2 s = 4 beats at 120
    auto on = onsets (rec.data(), (int) rec.size(), 0.15f);
    bool ok = on.size() >= 3;
    juce::String where;
    for (auto o : on)
    {
        const double ppqAt = startPpq + o * (120.0 / 60.0 / sr);
        const double off = ppqAt - std::round (ppqAt);
        where << juce::String (ppqAt, 2) << " ";
        ok = ok && std::abs (off) < 0.02;
    }
    CHECK (ok, "clicks land on host beats: " + where);

    // tempo change 120 -> 90: re-rendered within a second, still on the beat
    head.bpm = 90;
    for (int t = 0; t < 60; ++t)
    {
        process (5, nullptr);
        pump (20);
    }
    const double start2 = head.ppq;
    rec.clear();
    process (300, &rec);
    on = onsets (rec.data(), (int) rec.size(), 0.15f);
    ok = on.size() >= 3;
    where.clear();
    for (auto o : on)
    {
        const double ppqAt = start2 + o * (90.0 / 60.0 / sr);
        where << juce::String (ppqAt, 2) << " ";
        ok = ok && std::abs (ppqAt - std::round (ppqAt)) < 0.02;
    }
    CHECK (ok, "after tempo change to 90 BPM, still on the beat: " + where);

    // the host stops: the preview stops with it; the host starts: it plays again, on the beat
    head.playing = false;
    process (20, nullptr);
    CHECK (! proc.isPreviewing(), "host Stop stops the preview (sync mode)");
    head.ppq = 32.0;
    head.playing = true;
    const double start3 = head.ppq;
    rec.clear();
    process (300, &rec);
    CHECK (proc.isPreviewing(), "host Play starts it again");
    on = onsets (rec.data(), (int) rec.size(), 0.15f);
    ok = on.size() >= 3;
    where.clear();
    for (auto o : on)
    {
        const double ppqAt = start3 + o * (90.0 / 60.0 / sr);
        where << juce::String (ppqAt, 2) << " ";
        ok = ok && std::abs (ppqAt - std::round (ppqAt)) < 0.02;
    }
    CHECK (ok, "after host Play, on the beat: " + where);

    proc.stop();
    process (10, nullptr);
    CHECK (! proc.isPreviewing(), "stop");

    // player menu: loop length /2 re-renders the playing loop at half its beats
    head.playing = false;
    process (2, nullptr);   // the host Stop first (it would stop the new preview too)
    proc.play (info (f, "Loop", 100, 2.4));
    for (int t = 0; t < 300 && ! (proc.isPreviewing() && proc.previewComplete()); ++t)
    {
        pump (20);
        process (1, nullptr);
    }
    const double before = proc.playLength();
    proc.setLength (f.getFullPathName(), 0.5);
    for (int t = 0; t < 300 && std::abs (proc.playLength() - before) < 0.01; ++t)
    {
        pump (20);
        process (1, nullptr);
    }
    CHECK (proc.currentSample() != nullptr && proc.currentSample()->lenMult == 0.5
               && std::abs (proc.playLength() - 2 * 60.0 / 90.0) < 0.02,
           "len /2: 4 beats at 100 counted as 2 beats -> " + juce::String (proc.playLength(), 3) + " s at 90 BPM");

    // Loop preview off: a loop plays once and stops
    proc.settings.setProperty ("loopPreview", false, nullptr);
    pump (300);   // the processor's timer passes the setting to the audio thread
    proc.play (info (f, "Loop", 100, 2.4));
    for (int t = 0; t < 300 && ! proc.previewComplete(); ++t)
    {
        pump (20);
        process (1, nullptr);
    }
    process ((int) (3.5 * sr / block), nullptr);
    CHECK (! proc.isPreviewing(), "Loop preview off: the loop played once and stopped");
    proc.settings.setProperty ("loopPreview", true, nullptr);
    proc.setPlayHead (nullptr);
}

static void testService()
{
    if (juce::SystemStats::getEnvironmentVariable ("FETCH_TEST_SERVICE", {}) != "1")
    {
        std::cout << "service (skipped, set FETCH_TEST_SERVICE=1)\n";
        return;
    }
    std::cout << "service\n";
    FetchClient c;
    auto r = c.browse ("", "+loop bpm:100", 0, 5);
    CHECK (r.error.isEmpty() && r.samples.size() > 0, "browse +loop bpm:100 -> " + juce::String (r.total) + " " + r.error);
    auto t = c.browse ("warm pad", "", 0, 3);
    CHECK (t.error.isEmpty() && t.samples.size() == 3, "text search: " + (t.samples.isEmpty() ? t.error : t.samples[0].name));
    if (r.samples.isEmpty())
        return;
    auto s = r.samples[0];
    auto sim = c.similar (s.path, { "timbre" }, 5);
    CHECK (sim.error.isEmpty() && sim.samples.size() == 5, "similar to " + s.name);
    Renderer rend;
    juce::String err;
    auto plan = planFor (s, 128, 48000, true, "Am");
    auto p = rend.render (s, plan, err);
    CHECK (p != nullptr, "render real loop " + s.name + " " + err);
    if (p)
    {
        const double want = plan.beats * 60.0 / 128.0 * 48000;
        CHECK (std::abs (p->audio.getNumSamples() - want) < 2, juce::String (plan.beats) + " beats at 128 BPM = "
                                                                  + juce::String (p->audio.getNumSamples()) + " samples");
    }
}

// Loads the built .vst3 like a host would (JUCE's VST3 hosting): instantiate, process, open the editor.
static void testVst3 (const juce::String& path)
{
    std::cout << "vst3 bundle\n";
    juce::VST3PluginFormat fmt;
    juce::OwnedArray<juce::PluginDescription> found;
    fmt.findAllTypesForFile (found, path);
    CHECK (found.size() == 1, "one plugin in " + path);
    if (found.isEmpty())
        return;
    auto& d = *found[0];
    CHECK (d.name == "Fetch" && d.isInstrument, "name Fetch, instrument: " + d.name + " / " + d.category);
    juce::String err;
    auto inst = fmt.createInstanceFromDescription (d, 48000, 512, err);
    CHECK (inst != nullptr, "instantiate " + err);
    if (! inst)
        return;
    inst->enableAllBuses();
    inst->prepareToPlay (48000, 512);
    juce::AudioBuffer<float> buf (juce::jmax (2, inst->getTotalNumOutputChannels()), 512);
    juce::MidiBuffer midi;
    for (int i = 0; i < 20; ++i)
        inst->processBlock (buf, midi);
    CHECK (buf.getMagnitude (0, 512) == 0.0f, "silent until a sample is played");
    juce::MemoryBlock state;
    inst->getStateInformation (state);
    inst->setStateInformation (state.getData(), (int) state.getSize());
    CHECK (state.getSize() > 0, "state save/restore");
    if (juce::SystemStats::getEnvironmentVariable ("DISPLAY", {}).isNotEmpty()
        || juce::SystemStats::getEnvironmentVariable ("WAYLAND_DISPLAY", {}).isNotEmpty())
    {
        std::unique_ptr<juce::AudioProcessorEditor> ed (inst->createEditorAndMakeActive());
        CHECK (ed != nullptr && ed->getWidth() > 600, "editor opens");
        juce::MessageManager::getInstance()->runDispatchLoopUntil (300);
        if (ed)
            inst->editorBeingDeleted (ed.get());
    }
    inst->releaseResources();
}

// The editor in a real window, driven by mouse/key events through its peer (as the OS delivers them):
// clicking the sidebar must not take the arrow keys from the results list.
template <typename T>
static T* findChild (juce::Component& root, std::function<bool (T&)> pred)
{
    for (auto* c : root.getChildren())
    {
        if (auto* t = dynamic_cast<T*> (c); t != nullptr && pred (*t))
            return t;
        if (auto* r = findChild<T> (*c, pred))
            return r;
    }
    return nullptr;
}

static void testUiFocus()
{
    std::cout << "ui: arrow keys stay on the list\n";
    FetchProcessor proc;
    proc.settings.setProperty ("autoPlay", false, nullptr);
    juce::DocumentWindow win ("Fetch test", juce::Colours::black, 0);
    win.setUsingNativeTitleBar (true);
    win.setContentNonOwned (proc.createEditor(), true);
    std::unique_ptr<juce::Component> owner (win.getContentComponent());
    win.setVisible (true);
    win.toFront (true);
    auto pump = [] (int ms) { juce::MessageManager::getInstance()->runDispatchLoopUntil (ms); };
    auto* table = findChild<juce::TableListBox> (win, [] (auto&) { return true; });
    for (int i = 0; i < 60 && (table == nullptr || table->getNumRows() < 5); ++i)
        pump (100);
    CHECK (table != nullptr && table->getNumRows() >= 5, "results loaded");
    if (table == nullptr || table->getNumRows() < 5)
        return;
    auto* peer = win.getPeer();
    auto* sw = findChild<juce::ToggleButton> (win, [] (auto& b) { return b.getButtonText() == "Drag matched audio"; });
    auto* vp = findChild<juce::Viewport> (win, [] (auto&) { return true; });
    CHECK (peer != nullptr && sw != nullptr && vp != nullptr, "window, switch and sidebar found");
    if (peer == nullptr || sw == nullptr || vp == nullptr)
        return;

    vp->setViewPosition (0, juce::jmax (0, sw->getBottom() - vp->getHeight() + 60));   // the switch in view
    pump (50);
    table->selectRow (0);
    table->grabKeyboardFocus();
    const bool before = sw->getToggleState();
    auto click = [&] (juce::Component& c) {
        const auto pos = peer->globalToLocal (c.localPointToGlobal (c.getLocalBounds().getCentre()).toFloat());
        const auto t = juce::Time::currentTimeMillis();
        peer->handleMouseEvent (juce::MouseInputSource::InputSourceType::mouse, pos,
                                juce::ModifierKeys (juce::ModifierKeys::leftButtonModifier), 0.0f, 0.0f, t);
        peer->handleMouseEvent (juce::MouseInputSource::InputSourceType::mouse, pos, juce::ModifierKeys(), 0.0f, 0.0f, t + 50);
        pump (100);
    };
    click (*sw);
    CHECK (sw->getToggleState() != before, "clicking a sidebar switch works");
    const int scrollBefore = vp->getViewPositionY();
    for (int i = 0; i < 3; ++i)
    {
        peer->handleKeyPress (juce::KeyPress (juce::KeyPress::downKey));
        pump (30);
    }
    CHECK (table->getSelectedRow() == 3, "Down x3 after a sidebar click moves the list to row 3 (got "
                                             + juce::String (table->getSelectedRow()) + ")");
    CHECK (vp->getViewPositionY() == scrollBefore, "the sidebar did not scroll");
    click (*sw);   // restore
    CHECK (sw->getToggleState() == before, "switch restored");

    // Typing into the search field when the host hands focus to the window only after the click
    // (Bitwig): JUCE restores the previously focused list, which used to swallow the typing.
    auto* search = findChild<juce::TextEditor> (win, [] (auto&) { return true; });
    CHECK (search != nullptr, "search field found");
    if (search != nullptr)
    {
        const auto oldText = search->getText();
        search->setText ({}, false);
        table->grabKeyboardFocus();
        peer->handleFocusLoss();
        click (*search);
        peer->handleFocusGain();
        pump (200);
        for (auto ch : juce::String ("snare"))
        {
            peer->handleKeyPress (juce::KeyPress (ch, 0, ch));
            pump (20);
        }
        CHECK (search->getText() == "snare", "typed into the search after a focus change: '" + search->getText() + "'");
        CHECK (search->hasKeyboardFocus (false), "the search field keeps the focus");
        search->setText (oldText, false);
        // a click on a row gives the list the keys back
        if (auto* rowComp = table->getComponentForRowNumber (1))
            click (*rowComp);
        pump (100);
        CHECK (table->hasKeyboardFocus (true), "clicking a row moves the keys to the list");
    }

    // Down in the search field (after typing) goes into the list, as in the Fetch window
    if (search != nullptr)
    {
        const auto oldText = search->getText();
        click (*search);
        search->setText ("snare", true);   // typed: the search is still pending
        table->deselectAllRows();
        peer->handleKeyPress (juce::KeyPress (juce::KeyPress::downKey));
        for (int i = 0; i < 50 && table->getSelectedRow() < 0; ++i)
            pump (100);
        CHECK (table->hasKeyboardFocus (true), "Down in the search field moves the keys to the list");
        CHECK (table->getSelectedRow() == 0, "... and selects the first result (got " + juce::String (table->getSelectedRow()) + ")");
        peer->handleKeyPress (juce::KeyPress (juce::KeyPress::downKey));
        pump (50);
        CHECK (table->getSelectedRow() == 1, "the next Down browses the results");
        search->setText (oldText, true);
        pump (400);
    }

    // Edit tags: E opens the dialog, Esc closes it
    peer->handleKeyPress (juce::KeyPress ('e', 0, 'e'));
    pump (100);
    CHECK (findChild<TagEditor> (win, [] (auto&) { return true; }) != nullptr, "E opens Edit tags");
    if (auto* te = findChild<TagEditor> (win, [] (auto&) { return true; }))
    {
        auto* chip = findChild<Chip> (*te, [] (auto& c) { return c.getButtonText() == "Loop" || c.getButtonText() == "One-shot"; });
        CHECK (chip != nullptr, "Edit tags shows the Type chips");
        peer->handleKeyPress (juce::KeyPress (juce::KeyPress::escapeKey));
        pump (100);
    }
    CHECK (findChild<TagEditor> (win, [] (auto&) { return true; }) == nullptr, "Esc closes it without saving");
    win.setVisible (false);
}

// The sidebar layout and the Host sync switches follow the user to new instances (Prefs.h)
static void testPrefs()
{
    std::cout << "prefs: the last-used settings follow the user\n";
    CHECK (prefs::file().getFullPathName().startsWith (tmpDir().getFullPathName()), "test prefs in the temp folder");
    juce::MemoryBlock projectState;
    {
        FetchProcessor proc;   // a project saved with the defaults
        proc.getStateInformation (projectState);
    }
    {
        FetchProcessor proc;   // the user changes things in another instance
        std::unique_ptr<juce::AudioProcessorEditor> ed (proc.createEditor());
        juce::MessageManager::getInstance()->runDispatchLoopUntil (100);
        // without a search service (CI) the Type chips have no catalog to come from: give the sidebar one
        if (findChild<Chip> (*ed, [] (auto& c) { return c.getButtonText() == "Loop"; }) == nullptr)
            if (auto* sb = findChild<Sidebar> (*ed, [] (auto&) { return true; }))
            {
                sb->setCatalog (juce::JSON::parse (R"({"types": [{"name": "loop", "label": "Loop"},
                                                                 {"name": "oneshot", "label": "One-shot"}]})"));
                juce::MessageManager::getInstance()->runDispatchLoopUntil (100);
            }
        auto* sync = findChild<juce::ToggleButton> (*ed, [] (auto& b) { return b.getButtonText() == "Sync to host tempo"; });
        auto* head = findChild<juce::Button> (*ed, [] (auto& b) { return b.getButtonText() == "Host sync"; });
        auto* loop = findChild<Chip> (*ed, [] (auto& c) { return c.getButtonText() == "Loop"; });
        CHECK (sync != nullptr && head != nullptr && loop != nullptr, "switch, Host sync head and Loop chip found");
        if (sync == nullptr || head == nullptr || loop == nullptr)
            return;
        sync->triggerClick();
        loop->triggerClick();
        juce::MessageManager::getInstance()->runDispatchLoopUntil (100);
        head->triggerClick();
        juce::MessageManager::getInstance()->runDispatchLoopUntil (100);
        proc.settings.setProperty ("projectKey", "Dm", nullptr);
    }
    auto check = [] (FetchProcessor& proc, const juce::String& when) {
        CHECK (! proc.syncOn(), when + ": Sync to host tempo off as last left");
        const auto f = juce::JSON::parse (proc.settings.getProperty ("filters").toString());
        CHECK (f["type"].toString() == "loop", when + ": the Loop filter as last left");
        CHECK (f["collapsed"].toString().contains ("Host sync"), when + ": Host sync collapsed as last left");
        std::unique_ptr<juce::AudioProcessorEditor> ed (proc.createEditor());
        juce::MessageManager::getInstance()->runDispatchLoopUntil (100);
        auto* sync = findChild<juce::ToggleButton> (*ed, [] (auto& b) { return b.getButtonText() == "Sync to host tempo"; });
        CHECK (sync != nullptr && ! sync->getToggleState() && ! sync->isVisible(), when + ": the editor shows it");
    };
    {
        FetchProcessor proc;
        check (proc, "new instance");
        CHECK (proc.projectKey() == "Dm", "new instance: the project key last used");
    }
    {
        FetchProcessor proc;   // a project saved earlier, reopened
        proc.setStateInformation (projectState.getData(), (int) projectState.getSize());
        check (proc, "project reopened");
        CHECK (proc.projectKey().isEmpty(), "project reopened: its own project key");
    }
    prefs::file().deleteFile();
    prefs::cache() = juce::var (new juce::DynamicObject());
}

// FETCH_SNAPSHOT=out.png: the editor with real search results, rendered to an image.
static void snapshot (const juce::String& out)
{
    FetchProcessor proc;
    proc.settings.setProperty ("query", "", nullptr);
    proc.settings.setProperty ("projectKey", "Am", nullptr);
    if (auto f = juce::SystemStats::getEnvironmentVariable ("FETCH_SNAPSHOT_FILTERS", {}); f.isNotEmpty())
        proc.settings.setProperty ("filters", f, nullptr);
    if (auto ap = juce::SystemStats::getEnvironmentVariable ("FETCH_SNAPSHOT_APPEARANCE", {}); ap.isNotEmpty())
        tmpDir().getChildFile ("config/samplehaku/appearance.json").replaceWithText (ap);   // e.g. {"ui_scale":1.25}
    if (auto v = juce::SystemStats::getEnvironmentVariable ("FETCH_SNAPSHOT_VIEW", {}); v.isNotEmpty())
        proc.settings.setProperty ("view", v, nullptr);   // e.g. d:/path (a Folders tree folder)
    if (auto hgt = juce::SystemStats::getEnvironmentVariable ("FETCH_SNAPSHOT_HEIGHT", {}).getIntValue(); hgt > 0)
        proc.settings.setProperty ("height", hgt, nullptr);
    if (auto wid = juce::SystemStats::getEnvironmentVariable ("FETCH_SNAPSHOT_WIDTH", {}).getIntValue(); wid > 0)
        proc.settings.setProperty ("width", wid, nullptr);
    std::unique_ptr<juce::AudioProcessorEditor> ed (proc.createEditor());
    for (int i = 0; i < 40; ++i)
        juce::MessageManager::getInstance()->runDispatchLoopUntil (100);
    if (juce::SystemStats::getEnvironmentVariable ("FETCH_SNAPSHOT_PLAY", {}) == "1")
    {
        // select the first row (plays it) and let the render finish
        for (auto* c : ed->getChildren())
            if (auto* tl = dynamic_cast<juce::TableListBox*> (c))
                tl->selectRow (2);
        juce::AudioBuffer<float> buf (2, 512);
        juce::MidiBuffer midi;
        proc.prepareToPlay (48000, 512);
        for (int i = 0; i < 30; ++i)
        {
            juce::MessageManager::getInstance()->runDispatchLoopUntil (50);
            for (int b = 0; b < 4; ++b)
                proc.processBlock (buf, midi);
        }
    }
    if (juce::SystemStats::getEnvironmentVariable ("FETCH_SNAPSHOT_TAGS", {}) == "1")
    {
        for (auto* c : ed->getChildren())
            if (auto* tl = dynamic_cast<juce::TableListBox*> (c))
                tl->selectRow (5);
        ed->keyPressed (juce::KeyPress ('e', 0, 'e'));
        for (int i = 0; i < 5; ++i)
            juce::MessageManager::getInstance()->runDispatchLoopUntil (100);
    }
    if (juce::SystemStats::getEnvironmentVariable ("FETCH_SNAPSHOT_PLAYER", {}) == "1")
    {
        // the player menu (a call-out box, its own window): saved next to the snapshot as <name>-player.png
        if (auto* b = findChild<juce::Button> (*ed, [] (auto& x) { return x.getTooltip() == "Player options"; }))
            b->triggerClick();
        for (int i = 0; i < 5; ++i)
            juce::MessageManager::getInstance()->runDispatchLoopUntil (100);
        auto& desk = juce::Desktop::getInstance();
        for (int i = 0; i < desk.getNumComponents(); ++i)
            if (auto* box = dynamic_cast<juce::CallOutBox*> (desk.getComponent (i)))
            {
                auto pimg = box->createComponentSnapshot (box->getLocalBounds(), true, 1.0f);
                juce::File pf (out.upToLastOccurrenceOf (".", false, false) + "-player.png");
                pf.deleteFile();
                juce::FileOutputStream pos (pf);
                juce::PNGImageFormat().writeImageToStream (pimg, pos);
                std::cout << "snapshot " << pf.getFullPathName() << "\n";
            }
    }
    auto img = ed->createComponentSnapshot (ed->getLocalBounds(), true, 1.0f);
    juce::File f (out);
    f.deleteFile();
    juce::FileOutputStream os (f);
    juce::PNGImageFormat().writeImageToStream (img, os);
    std::cout << "snapshot " << out << "\n";
}

int main (int argc, char** argv)
{
    tmpDir().getChildFile ("config").deleteRecursively();   // every run starts from no prefs
   #if JUCE_LINUX
    setenv ("XDG_CONFIG_HOME", tmpDir().getChildFile ("config").getFullPathName().toRawUTF8(), 1);   // not the user's prefs
   #else
    _putenv_s ("FETCH_NO_PREFS", "1");
   #endif
    juce::ScopedJuceInitialiser_GUI init;
    testKeys();
    testPlans();
    testDragItem();
    testRender();
    testHostSync();
    testService();
    if (argc > 1)
        testVst3 (argv[1]);
    if (juce::SystemStats::getEnvironmentVariable ("FETCH_TEST_SERVICE", {}) == "1"
        && juce::SystemStats::getEnvironmentVariable ("DISPLAY", {}).isNotEmpty())
        testUiFocus();
   #if JUCE_LINUX
    testPrefs();
   #endif
    if (auto snap = juce::SystemStats::getEnvironmentVariable ("FETCH_SNAPSHOT", {}); snap.isNotEmpty())
        snapshot (snap);
    std::cout << (failures ? "FAILED: " + std::to_string (failures) : std::string ("all passed")) << "\n";
    return failures ? 1 : 0;
}
