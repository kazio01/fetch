#include "Preview.h"
#include <signalsmith-stretch/signalsmith-stretch.h>
#include <cmath>

namespace keys
{
static const char* noteNames[] = { "C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B" };

std::pair<int, bool> parse (const juce::String& key)
{
    auto k = key.trim().replace ("~", "");
    if (k.isEmpty())
        return { -1, false };
    bool minor = k.endsWithChar ('m') && ! k.endsWithIgnoreCase ("maj");
    if (minor)
        k = k.dropLastCharacters (1);
    static const std::pair<const char*, int> aliases[] = {
        { "C", 0 }, { "C#", 1 }, { "Db", 1 }, { "D", 2 }, { "D#", 3 }, { "Eb", 3 }, { "E", 4 }, { "F", 5 },
        { "F#", 6 }, { "Gb", 6 }, { "G", 7 }, { "G#", 8 }, { "Ab", 8 }, { "A", 9 }, { "A#", 10 }, { "Bb", 10 },
        { "B", 11 }, { "H", 11 } };
    for (auto& [n, pc] : aliases)
        if (k == n)
            return { pc, minor };
    return { -1, false };
}

juce::String name (int pc, bool minor)
{
    return juce::String (noteNames[((pc % 12) + 12) % 12]) + (minor ? "m" : "");
}

int semitonesBetween (const juce::String& from, const juce::String& to)
{
    auto [a, aMinor] = parse (from);
    auto [b, bMinor] = parse (to);
    if (a < 0 || b < 0)
        return 0;
    if (aMinor && ! bMinor)
        b += 9;   // relative minor of the project's major key
    else if (! aMinor && bMinor)
        b += 3;   // relative major of the project's minor key
    int d = ((b - a) % 12 + 12) % 12;
    return d > 5 ? d - 12 : d;
}

juce::StringArray all()
{
    juce::StringArray out;
    for (int pc = 0; pc < 12; ++pc)
    {
        out.add (name (pc, false));
        out.add (name (pc, true));
    }
    return out;
}
} // namespace keys

juce::String MatchPlan::suffix() const
{
    juce::StringArray parts;
    if (tempo)
        parts.add (juce::String (hostBpm, std::abs (hostBpm - std::round (hostBpm)) < 0.05 ? 0 : 1) + " BPM");
    if (semitones != 0)
        parts.add ((semitones > 0 ? "+" : "") + juce::String (semitones) + " st");
    return parts.joinIntoString (", ");
}

MatchPlan planFor (const SampleInfo& s, double hostBpm, double sampleRate, bool syncTempo, const juce::String& projectKey)
{
    MatchPlan p;
    p.hostBpm = hostBpm > 1.0 ? hostBpm : 120.0;
    p.targetSampleRate = sampleRate > 0 ? sampleRate : 44100.0;

    if (syncTempo && s.isLoop() && s.bpm > 0 && s.dur > 0.2)
    {
        // A 70 BPM loop in a 140 BPM project is counted at 140 (and vice versa): half / double tempo, but only
        // when that lands within ~25 % of the host and closer than the loop's own tempo. Otherwise beat for
        // beat: a 174 BPM break in a 120 project is slowed down, not played double-time as 87.
        // s.lenMult (the player menu's /2, x2) overrides this: the loop counted at bpm · lenMult.
        double best = s.bpm;
        if (s.lenMult > 0)
            best = s.bpm * s.lenMult;
        else
            for (double m : { 0.5, 2.0 })
            {
                const double d = std::abs (std::log (p.hostBpm / (s.bpm * m)));
                if (d < 0.22 && d < std::abs (std::log (p.hostBpm / best)))
                    best = s.bpm * m;
            }
        double beats = s.dur * best / 60.0;
        const double whole = std::round (beats);
        beats = (whole >= 1 && std::abs (beats - whole) < 0.12) ? whole : std::max (0.25, std::round (beats * 4) / 4);
        p.sampleBpm = best;
        p.beats = beats;
        p.tempo = true;
    }
    if (projectKey.isNotEmpty() && s.key.isNotEmpty())
        p.semitones = keys::semitonesBetween (s.key, projectKey);
    return p;
}

Renderer::Renderer()
{
    formats.registerBasicFormats();
}

juce::File Renderer::renderFolder()
{
    // Not a cache: the DAW keeps referring to dropped files, so they are never deleted automatically.
    return juce::File::getSpecialLocation (juce::File::userDocumentsDirectory)
        .getChildFile ("Fetch Collections").getChildFile ("Rendered");
}

namespace
{
// A loop as an endless input: index i reads sample (offset + i) mod length.
struct LoopInput
{
    const float* const* ch;
    int len;
    juce::int64 offset;
    struct Channel
    {
        const float* d;
        int len;
        juce::int64 offset;
        float operator[] (int i) const
        {
            auto k = (offset + i) % len;
            return d[k < 0 ? k + len : k];
        }
    };
    Channel operator[] (int c) const { return { ch[c], len, offset }; }
};
} // namespace

std::shared_ptr<Preview> Renderer::render (const SampleInfo& s, const MatchPlan& plan, juce::String& error,
                                           const std::function<bool()>& shouldExit,
                                           const std::function<void (std::shared_ptr<Preview>)>& onPlayable)
{
    auto cancelled = [&] { return shouldExit && shouldExit(); };
    const auto file = localFile (s.path);
    std::unique_ptr<juce::AudioFormatReader> reader (formats.createReaderFor (file));
    if (reader == nullptr)
    {
        error = file.existsAsFile() ? "Can't read " + file.getFileName() : "File not found: " + file.getFullPathName();
        return nullptr;
    }
    const double fileRate = reader->sampleRate > 0 ? reader->sampleRate : 44100.0;
    const auto len = (int) juce::jmin<juce::int64> (reader->lengthInSamples, (juce::int64) (fileRate * 180));
    if (len <= 0)
    {
        error = "Empty file: " + file.getFileName();
        return nullptr;
    }

    const int pad = 256;   // the interpolator reads ahead
    juce::AudioBuffer<float> src (2, len + pad);
    src.clear();
    const double rate = plan.targetSampleRate;

    // Unchanged audio (one-shots, loops without sync): played while it is still being read. Over a network
    // share a 10 MB file takes over a second to arrive; the first block is there in ~40 ms.
    if (! plan.changesAudio() && onPlayable)
    {
        auto out = std::make_shared<Preview>();
        out->info = s;
        out->plan = plan;
        out->loop = s.isLoop();
        const double ratio = fileRate / rate;
        const bool same = std::abs (fileRate - rate) < 0.5;
        const int outLen = same ? len : (int) std::floor (len / ratio);
        if (outLen > 0)
        {
            out->audio.setSize (2, outLen);
            out->audio.clear();
            juce::LagrangeInterpolator interp[2];
            const int block = 32768;
            int inUsed[2] = { 0, 0 }, outDone = 0;
            bool published = false;
            for (int got = 0; got < len;)
            {
                const int n = juce::jmin (block, len - got);
                reader->read (&src, got, n, got, true, true);
                if (reader->numChannels == 1)
                    src.copyFrom (1, got, src, 0, got, n);
                got += n;
                // what can be made from the frames read so far (the last block also uses the silent padding)
                const int target = got >= len ? outLen : (same ? got : juce::jmax (0, (int) ((got - 8) / ratio)));
                if (target > outDone)
                {
                    for (int c = 0; c < 2; ++c)
                    {
                        if (same)
                            out->audio.copyFrom (c, outDone, src, c, outDone, target - outDone);
                        else
                            inUsed[c] += interp[c].process (ratio, src.getReadPointer (c) + inUsed[c],
                                                            out->audio.getWritePointer (c) + outDone, target - outDone);
                    }
                    outDone = target;
                    out->ready.store (outDone, std::memory_order_release);
                }
                if (! published && outDone > 0)
                {
                    published = true;
                    onPlayable (out);
                }
                if (cancelled())
                    return nullptr;
            }
            out->ready.store (outLen, std::memory_order_release);
            return out;
        }
    }

    reader->read (&src, 0, len, 0, true, true);
    if (reader->numChannels == 1)
        src.copyFrom (1, 0, src, 0, 0, len);
    if (cancelled())
        return nullptr;

    auto out = std::make_shared<Preview>();
    out->info = s;
    out->plan = plan;
    out->loop = s.isLoop();
    out->synced = plan.tempo;

    // Loops that change: streamed in chunks over the loop repeated endlessly, so playback can start after
    // the first chunk (browsing loops quickly), and the loop point is seamless: the start is preceded by
    // the loop's own end, and the end runs on into the start. The stretcher also does the sample-rate
    // change (as part of its transposition), which saves a slow resampling pass before the first sound.
    if (out->loop && plan.changesAudio())
    {
        signalsmith::stretch::SignalsmithStretch<float> st;
        st.presetDefault (2, (float) rate);
        const int inLen = len;
        const int outLen = plan.tempo ? (int) std::lround (plan.beats * 60.0 / plan.hostBpm * rate)
                                      : (int) std::lround (len * rate / fileRate);
        if (outLen > 0 && inLen >= 2 * st.seekLength())
        {
            st.setTransposeFactor ((float) (std::pow (2.0, plan.semitones / 12.0) * fileRate / rate));
            out->audio.setSize (2, outLen);
            out->audio.clear();
            const double ratio = (double) inLen / outLen;   // input frames per output frame
            const auto* src2 = src.getArrayOfReadPointers();
            const int inLat = st.inputLatency(), outLat = st.outputLatency();
            // history ending at input frame inLat: the stretcher's centre is then at frame 0
            st.seek (LoopInput { src2, inLen, (juce::int64) inLat - st.seekLength() }, st.seekLength(), ratio);
            const int chunk = 4096, total = outLat + outLen;
            const int firstPlayable = juce::jmin (outLen, 8192);
            juce::AudioBuffer<float> tmp (2, chunk);
            juce::int64 inDone = 0;
            bool published = false;
            for (int done = 0; done < total;)
            {
                const int n = juce::jmin (chunk, total - done);
                const auto inEnd = (juce::int64) std::llround ((done + n) * ratio);
                st.process (LoopInput { src2, inLen, inLat + inDone }, (int) (inEnd - inDone), tmp.getArrayOfWritePointers(), n);
                for (int c = 0; c < 2; ++c)
                {
                    const int from = juce::jmax (0, outLat - done);   // the first outLat frames are latency
                    const int to = done + from - outLat;
                    if (n > from)
                        out->audio.copyFrom (c, to, tmp, c, from, juce::jmin (n - from, outLen - to));
                }
                inDone = inEnd;
                done += n;
                out->ready.store (juce::jlimit (0, outLen, done - outLat), std::memory_order_release);
                if (! published && onPlayable && out->ready.load() >= firstPlayable)
                {
                    published = true;
                    onPlayable (out);
                }
                if (cancelled())
                    return nullptr;
            }
            return out;
        }
    }

    // 1. to the host's sample rate
    juce::AudioBuffer<float> res;
    if (std::abs (fileRate - rate) < 0.5)
    {
        res.setSize (2, len);
        for (int c = 0; c < 2; ++c)
            res.copyFrom (c, 0, src, c, 0, len);
    }
    else
    {
        const double ratio = fileRate / rate;
        const int outLen = (int) std::floor (len / ratio);
        res.setSize (2, outLen);
        // Unchanged audio is only previewed (never written), and long loops would wait for the slow sinc
        // resampler, so it gets the fast one; what is stretched or saved keeps the windowed sinc.
        const bool fast = ! plan.changesAudio();
        for (int c = 0; c < 2; ++c)
        {
            if (fast)
            {
                juce::LagrangeInterpolator interp;
                interp.process (ratio, src.getReadPointer (c), res.getWritePointer (c), outLen);
            }
            else
            {
                juce::WindowedSincInterpolator interp;
                interp.process (ratio, src.getReadPointer (c), res.getWritePointer (c), outLen);
            }
        }
    }
    if (cancelled())
        return nullptr;

    if (! plan.changesAudio())
    {
        out->audio = std::move (res);
        out->ready = out->audio.getNumSamples();
        return out;
    }

    // 2. tempo and key (Signalsmith Stretch, offline "exact" mode: output length is exact)
    const int inLen = res.getNumSamples();
    const int outLen = plan.tempo ? (int) std::lround (plan.beats * 60.0 / plan.hostBpm * rate) : inLen;
    if (outLen <= 0)
    {
        error = "Too short to stretch";
        return nullptr;
    }
    signalsmith::stretch::SignalsmithStretch<float> st;
    st.presetDefault (2, (float) rate);
    st.setTransposeSemitones ((float) plan.semitones);

    // Others (and very short loops) in one go; short loops are rendered twice in a row and the second
    // round kept, so the loop point is seamless.
    const int copies = out->loop ? 2 : 1;
    juce::AudioBuffer<float> in (2, inLen * copies);
    for (int k = 0; k < copies; ++k)
        for (int c = 0; c < 2; ++c)
            in.copyFrom (c, k * inLen, res, c, 0, inLen);
    juce::AudioBuffer<float> stretched (2, outLen * copies);
    stretched.clear();

    if (! st.exact (in.getArrayOfReadPointers(), in.getNumSamples(), stretched.getArrayOfWritePointers(), stretched.getNumSamples()))
    {
        // too short for the stretcher: play the resampled original instead
        out->plan.tempo = false;
        out->plan.semitones = 0;
        out->synced = false;
        out->audio = std::move (res);
        out->ready = out->audio.getNumSamples();
        return out;
    }
    if (cancelled())
        return nullptr;

    out->audio.setSize (2, outLen);
    for (int c = 0; c < 2; ++c)
        out->audio.copyFrom (c, 0, stretched, c, (copies - 1) * outLen, outLen);
    out->ready = outLen;
    return out;
}

juce::File Renderer::writeWav (const Preview& p, juce::String& error)
{
    auto dir = renderFolder();
    if (! dir.createDirectory())
    {
        error = "Can't create " + dir.getFullPathName();
        return {};
    }
    const auto stem = juce::File (p.info.path).getFileNameWithoutExtension();
    const auto name = juce::File::createLegalFileName (stem + " (" + p.plan.suffix() + ").wav");
    auto f = dir.getChildFile (name);
    f.deleteFile();

    juce::WavAudioFormat wav;
    std::unique_ptr<juce::OutputStream> os (f.createOutputStream());
    if (os == nullptr)
    {
        error = "Can't write " + f.getFullPathName();
        return {};
    }
    auto w = wav.createWriterFor (os, juce::AudioFormatWriterOptions().withSampleRate (p.plan.targetSampleRate)
                                          .withNumChannels (2).withBitsPerSample (24));
    if (w == nullptr)
    {
        error = "Can't write a WAV file";
        return {};
    }
    w->writeFromAudioSampleBuffer (p.audio, 0, p.audio.getNumSamples());
    return f;
}
