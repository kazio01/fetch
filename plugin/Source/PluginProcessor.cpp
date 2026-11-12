#include "PluginProcessor.h"
#include "PluginEditor.h"
#include "Prefs.h"
#include <cmath>

static juce::AudioProcessorValueTreeState::ParameterLayout makeLayout()
{
    juce::AudioProcessorValueTreeState::ParameterLayout l;
    l.add (std::make_unique<juce::AudioParameterFloat> (juce::ParameterID { "volume", 1 }, "Preview volume",
                                                        juce::NormalisableRange<float> (-60.0f, 6.0f, 0.1f, 2.0f), -6.0f,
                                                        juce::AudioParameterFloatAttributes().withLabel ("dB")));
    return l;
}

FetchProcessor::FetchProcessor()
    : AudioProcessor (BusesProperties().withOutput ("Output", juce::AudioChannelSet::stereo(), true)),
      juce::Thread ("Fetch render"),
      params (*this, nullptr, "Params", makeLayout())
{
    volumeDb = params.getRawParameterValue ("volume");
    applyPrefs();
    settings.addListener (this);
    startThread (juce::Thread::Priority::low);
    startTimerHz (5);
}

FetchProcessor::~FetchProcessor()
{
    *aliveFlag = false;
    stopTimer();
    ++warmId;
    warmPool.removeAllJobs (true, 3000);
    signalThreadShouldExit();
    notify();
    stopThread (5000);
}

bool FetchProcessor::isBusesLayoutSupported (const BusesLayout& layouts) const
{
    const auto& out = layouts.getMainOutputChannelSet();
    return out == juce::AudioChannelSet::stereo() || out == juce::AudioChannelSet::mono();
}

void FetchProcessor::prepareToPlay (double sampleRate, int)
{
    const bool changed = std::abs (rate.load() - sampleRate) > 0.5;
    rate = sampleRate;
    gain.reset (sampleRate, 0.03);
    gain.setCurrentAndTargetValue (juce::Decibels::decibelsToGain (volumeDb->load(), -60.0f));
    if (changed)   // re-render for the new rate
        juce::MessageManager::callAsync ([this] { settingsChanged(); });
}

// ---------------------------------------------------------------- audio thread

void FetchProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midi)
{
    juce::ScopedNoDenormals noDenormals;
    midi.clear();
    buffer.clear();
    const int n = buffer.getNumSamples();
    const double sr = getSampleRate() > 0 ? getSampleRate() : rate.load();

    double bpm = hostBpm.load(), ppq = 0.0;
    bool isPlaying = false, hasPpq = false;
    if (auto* ph = getPlayHead())
        if (auto info = ph->getPosition())
        {
            if (auto b = info->getBpm(); b && *b > 1.0)
            {
                bpm = *b;
                hostHasTempo = true;
            }
            isPlaying = info->getIsPlaying();
            if (auto q = info->getPpqPosition())
            {
                ppq = *q;
                hasPpq = true;
            }
        }
    hostBpm = bpm;
    hostPlaying = isPlaying;

    auto* pv = current.load (std::memory_order_acquire);
    if (const int t = trigger.load(); t != lastTrigger)
    {
        lastTrigger = t;
        playing = pv != nullptr;
        fadingOut = false;
        pos = 0.0;
        fade = 0.0f;
        lastPreview = pv;
        paused = false;
    }
    if (stopRequested.exchange (false) && playing)
        fadingOut = true;

    // Sync mode follows the host transport: Play starts the current sample in phase with the
    // arrangement (one-shots from their start), Stop stops it. While the host is stopped,
    // samples still preview freely, so browsing works.
    if (followHost.load() && isPlaying != wasHostPlaying && pv != nullptr)
    {
        if (isPlaying)
        {
            playing = true;
            fadingOut = false;
            paused = false;
            pos = 0.0;
            fade = 0.0f;
            lastPreview = pv;
        }
        else if (playing)
            fadingOut = true;
    }
    wasHostPlaying = isPlaying;
    if (pv != lastPreview && pv != nullptr && lastPreview != nullptr && playing)
    {
        // re-rendered (tempo or key changed): keep the same place in the loop
        pos = pos / juce::jmax (1, lastPreview->audio.getNumSamples()) * pv->audio.getNumSamples();
    }
    lastPreview = pv;
    audioHolds.store (pv, std::memory_order_release);
    if (! playing || pv == nullptr)
    {
        active = false;
        return;
    }
    active = true;

    const auto& a = pv->audio;
    const int len = a.getNumSamples();
    if (len < 2)
    {
        playing = false;
        return;
    }
    playLen = len / sr;
    if (const double s = seekTo.exchange (-1.0); s >= 0.0)
    {
        pos = s * len;
        fade = 0.0f;
    }
    if (paused.load())
    {
        playFrac = pos / len;
        return;   // silent, keeps its place
    }
    // Loop preview off: a loop plays once from its start (at the host tempo if stretched), not phase-locked
    const bool looping = pv->loop && loopPreview.load();
    const bool synced = pv->synced && looping && isPlaying && hasPpq && pv->plan.beats > 0;
    if (synced != wasSynced)
    {
        fade = 0.0f;   // jumping to the host's phase (or back): fade in instead of a click
        wasSynced = synced;
    }

    double idx, inc;
    if (synced)
    {
        // Phase-locked to the host: the loop plays as if it had started at bar 1, so any
        // whole-bar loop lines up with the arrangement wherever the transport is.
        const double beats = pv->plan.beats;
        double phase = std::fmod (ppq, beats) / beats;
        if (phase < 0)
            phase += 1.0;
        idx = phase * len;
        inc = (bpm / 60.0 / sr) / beats * len;
    }
    else
    {
        idx = pos;
        inc = 1.0;
    }

    gain.setTargetValue (juce::Decibels::decibelsToGain (volumeDb->load(), -60.0f));
    const float fadeStep = (float) (1.0 / (0.005 * sr));
    const int outCh = buffer.getNumChannels();
    const float* l = a.getReadPointer (0);
    const float* r = a.getReadPointer (1);
    const int ready = pv->ready.load (std::memory_order_acquire);   // still rendering beyond this
    for (int i = 0; i < n; ++i)
    {
        if (ready < len && idx + 1.0 >= ready)
        {
            // not rendered yet (the renderer runs far faster than real time, so only at the very start):
            // free-running waits here, synced keeps the host's phase; fade in when it arrives
            if (fadingOut)
            {
                playing = false;
                fadingOut = false;
                break;
            }
            fade = 0.0f;
            if (! synced)
                break;
            idx += inc;
            if (idx >= len)
                idx = std::fmod (idx, (double) len);
            continue;
        }
        if (idx >= len)
        {
            if (looping)
                idx = std::fmod (idx, (double) len);
            else
            {
                playing = false;
                break;
            }
        }
        const int i0 = (int) idx;
        const int i1 = i0 + 1 < len ? i0 + 1 : (looping ? 0 : i0);
        const float frac = (float) (idx - i0);
        float g = gain.getNextValue();
        if (fadingOut)
        {
            fade -= fadeStep * 0.5f;
            if (fade <= 0.0f)
            {
                playing = false;
                fadingOut = false;
                break;
            }
        }
        else if (fade < 1.0f)
            fade = juce::jmin (1.0f, fade + fadeStep);
        g *= fade;
        const float sl = (l[i0] + frac * (l[i1] - l[i0])) * g;
        const float sr2 = (r[i0] + frac * (r[i1] - r[i0])) * g;
        if (outCh >= 2)
        {
            buffer.addSample (0, i, sl);
            buffer.addSample (1, i, sr2);
        }
        else if (outCh == 1)
            buffer.addSample (0, i, 0.5f * (sl + sr2));
        idx += inc;
    }
    pos = idx;   // free-running continues from here (when the transport stops); synced recomputes from ppq
    playFrac = juce::jlimit (0.0, 1.0, std::fmod (idx, (double) len) / len);
    if (! playing)
        active = false;
}

// ---------------------------------------------------------------- message thread

MatchPlan FetchProcessor::planFor (const SampleInfo& s) const
{
    return ::planFor (s, hostBpm.load(), rate.load(), syncOn(), projectKey());
}

void FetchProcessor::play (const SampleInfo& s)
{
    wanted = s;
    const juce::ScopedLock sl (reqLock);
    pending = Request { s, planFor (s), true, ++requestId };
    notify();
}

void FetchProcessor::warm (const juce::StringArray& paths)
{
    const int id = ++warmId;
    warmPool.removeAllJobs (false, 0);              // only the newest request counts
    warmPool.addJob ([this, paths, id] {
        juce::HeapBlock<char> buf (1 << 20);
        for (auto& p : paths)
        {
            juce::FileInputStream in (localFile (p));
            if (! in.openedOk())
                continue;
            for (juce::int64 n = 0; n < (32 << 20);)
            {
                const int got = in.read (buf, 1 << 20);
                if (got <= 0)
                    break;
                n += got;
                if (warmId.load() != id && n > (4 << 20))
                    return;                         // a newer request: a long file can wait
            }
            if (warmId.load() != id)
                return;
        }
    });
}

void FetchProcessor::stop()
{
    stopRequested = true;
    paused = false;
}

void FetchProcessor::settingsChanged()
{
    if (! wanted || ! shown || shown->info.path != wanted->path)
        return;
    const auto plan = planFor (*wanted);
    const auto& old = shown->plan;
    if (plan.tempo == old.tempo && plan.semitones == old.semitones && std::abs (plan.hostBpm - old.hostBpm) < 0.01
        && std::abs (plan.sampleBpm - old.sampleBpm) < 0.01
        && std::abs (plan.targetSampleRate - old.targetSampleRate) < 0.5)
        return;
    const juce::ScopedLock sl (reqLock);
    // a play() the worker hasn't picked up yet stays a (re)start: only its plan changes
    const bool restart = pending && pending->restart;
    pending = Request { *wanted, plan, restart, ++requestId };
    notify();
}

void FetchProcessor::setLength (const juce::String& path, double lenMult)
{
    if (wanted && wanted->path == path)
    {
        wanted->lenMult = lenMult;
        settingsChanged();
    }
}

juce::String FetchProcessor::takeError()
{
    const juce::ScopedLock sl (reqLock);
    auto e = lastError;
    lastError.clear();
    return e;
}

void FetchProcessor::run()
{
    while (! threadShouldExit())
    {
        std::optional<Request> req;
        {
            const juce::ScopedLock sl (reqLock);
            req.swap (pending);
        }
        if (! req)
        {
            wait (-1);
            continue;
        }
        juce::String err;
        const int id = req->id;
        const bool restart = req->restart;
        bool published = false;
        auto publish = [this, restart, id] (std::shared_ptr<Preview> p) {
            juce::MessageManager::callAsync ([this, p, restart, id, alive = aliveFlag] {
                if (*alive && requestId.load() == id)
                    install (p, restart);
            });
        };
        // A newly chosen loop starts playing after its first chunk; a re-render (tempo/key change) of the
        // one playing is swapped in only when complete, so the loop keeps playing meanwhile.
        std::function<void (std::shared_ptr<Preview>)> early;
        if (restart)
            early = [&published, &publish] (std::shared_ptr<Preview> p) {
                published = true;
                publish (std::move (p));
            };
        auto p = renderer.render (req->info, req->plan, err,
                                  [this, id] { return threadShouldExit() || requestId.load() != id; }, early);
        if (p == nullptr)
        {
            if (err.isNotEmpty())
            {
                const juce::ScopedLock sl (reqLock);
                lastError = err;
            }
            continue;
        }
        if (! published)
            publish (p);
    }
}

void FetchProcessor::install (std::shared_ptr<Preview> p, bool restart)
{
    if (shown)
        shown->retiredAt = juce::Time::getMillisecondCounter();
    alive.push_back (p);
    shown = p;
    current.store (p.get(), std::memory_order_release);
    if (restart)
        ++trigger;
}

void FetchProcessor::timerCallback()
{
    followHost = syncOn();
    loopPreview = loopPreviewOn();
    // free previews the audio thread can no longer be reading (replaced > 2 s ago)
    const auto now = juce::Time::getMillisecondCounter();
    alive.erase (std::remove_if (alive.begin(), alive.end(), [&] (const std::shared_ptr<Preview>& p) {
                     return p != shown && p.get() != audioHolds.load (std::memory_order_acquire)
                            && p->retiredAt != 0 && now - p->retiredAt > 2000;
                 }),
                 alive.end());

    // host tempo changed: re-render once it has been stable for a moment (tempo ramps)
    if (shown && shown->synced && wanted && shown->info.path == wanted->path)
    {
        const double bpm = hostBpm.load();
        if (std::abs (bpm - shown->plan.hostBpm) > 0.01)
        {
            if (driftSince == 0)
                driftSince = now;
            else if (now - driftSince > 300)
            {
                driftSince = 0;
                settingsChanged();
            }
        }
        else
            driftSince = 0;
    }
}

juce::File FetchProcessor::fileForDrag (const SampleInfo& s, juce::String& error)
{
    const bool matched = settings.getProperty ("dragMatched", true);
    const auto plan = planFor (s);
    if (! matched || ! plan.changesAudio())
        return localFile (s.path);

    std::shared_ptr<Preview> p;
    if (shown && shown->info.path == s.path && shown->plan.tempo == plan.tempo && shown->plan.semitones == plan.semitones
        && std::abs (shown->plan.sampleBpm - plan.sampleBpm) < 0.01
        && std::abs (shown->plan.hostBpm - plan.hostBpm) < 0.01 && shown->complete())
        p = shown;
    else
    {
        Renderer r;   // not the worker's: this runs on the message thread
        p = r.render (s, plan, error);
        if (p == nullptr)
            return {};
    }
    return Renderer::writeWav (*p, error);
}

// ---------------------------------------------------------------- state

static const char* const userKeys[] = { "sync", "onlyTempo", "onlyKey", "dragMatched", "autoPlay", "loopPreview", "filters",
                                        "columns", "layoutVersion", "width", "height", "view", "query" };

void FetchProcessor::applyPrefs()
{
    const juce::ScopedValueSetter<bool> busy (applyingPrefs, true);
    for (auto* key : userKeys)
        if (const auto v = prefs::get (key); ! v.isVoid())
            settings.setProperty (key, v, nullptr);
    if (! settings.hasProperty ("projectKey"))   // a new instance: the key last used
        if (const auto k = prefs::get ("projectKey"); ! k.isVoid())
            settings.setProperty ("projectKey", k, nullptr);
}

void FetchProcessor::valueTreePropertyChanged (juce::ValueTree&, const juce::Identifier& key)
{
    if (applyingPrefs)
        return;
    for (auto* k : userKeys)
        if (key == juce::Identifier (k))
            prefs::set (key, settings.getProperty (key));
    if (key == juce::Identifier ("projectKey"))
        prefs::set (key, settings.getProperty (key));
}

void FetchProcessor::getStateInformation (juce::MemoryBlock& dest)
{
    juce::ValueTree root ("Fetch");
    root.appendChild (params.copyState(), nullptr);
    auto st = settings.createCopy();
    st.setProperty ("projectKey", projectKey(), nullptr);   // "Off" is a choice too: not the key last used
    root.appendChild (st, nullptr);
    if (auto xml = root.createXml())
        copyXmlToBinary (*xml, dest);
}

void FetchProcessor::setStateInformation (const void* data, int size)
{
    if (auto xml = getXmlFromBinary (data, size))
    {
        auto root = juce::ValueTree::fromXml (*xml);
        if (auto p = root.getChildWithName (params.state.getType()); p.isValid())
            params.replaceState (p);
        if (auto s = root.getChildWithName ("Settings"); s.isValid())
        {
            const juce::ScopedValueSetter<bool> busy (applyingPrefs, true);   // a project's state isn't "last used"
            settings.copyPropertiesFrom (s, nullptr);
        }
        applyPrefs();
    }
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new FetchProcessor();
}
