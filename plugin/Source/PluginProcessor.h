#pragma once
#include <juce_audio_processors/juce_audio_processors.h>
#include "FetchClient.h"
#include "Preview.h"

// Fetch as a VST3 instrument: a sample browser whose preview plays through the DAW,
// time-stretched to the host tempo, phase-locked to the host's beat position, and
// optionally transposed to the project key.
class FetchProcessor : public juce::AudioProcessor,
                       private juce::Timer,
                       private juce::Thread,
                       private juce::ValueTree::Listener
{
public:
    FetchProcessor();
    ~FetchProcessor() override;

    // ---- AudioProcessor
    void prepareToPlay (double sampleRate, int samplesPerBlock) override;
    void releaseResources() override {}
    bool isBusesLayoutSupported (const BusesLayout& layouts) const override;
    void processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) override;
    using juce::AudioProcessor::processBlock;
    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override { return true; }
    const juce::String getName() const override { return "Fetch"; }
    bool acceptsMidi() const override { return true; }
    bool producesMidi() const override { return false; }
    double getTailLengthSeconds() const override { return 0.0; }
    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram (int) override {}
    const juce::String getProgramName (int) override { return {}; }
    void changeProgramName (int, const juce::String&) override {}
    void getStateInformation (juce::MemoryBlock&) override;
    void setStateInformation (const void*, int) override;

    // ---- used by the editor (message thread)
    juce::AudioProcessorValueTreeState params;
    juce::ValueTree settings { "Settings" };   // sync, projectKey, onlyTempo, onlyKey, type, dragMatched, query
    FetchClient client;

    void play (const SampleInfo& s);   // render for the project and start
    void stop();
    void togglePause() { paused = ! paused.load(); }
    bool isPaused() const { return paused.load(); }
    void seek (double fraction) { seekTo = juce::jlimit (0.0, 1.0, fraction); }
    double playFraction() const { return playFrac.load(); }    // 0–1 within the playing sample
    double playLength() const { return playLen.load(); }       // seconds (as rendered)
    void settingsChanged();            // re-render the current sample if tempo/key settings changed
    // Player menu /2, x2, Auto: the sample's loop length (0 = automatic). Re-renders it if it is the one playing.
    void setLength (const juce::String& path, double lenMult);
    bool loopPreviewOn() const { return settings.getProperty ("loopPreview", true); }
    // Read these files into the operating system's file cache in the background (the rows the user is about
    // to reach): from a network share a loop takes up to a second to arrive, a file just read plays at once.
    void warm (const juce::StringArray& paths);
    bool isPreviewing() const { return active.load(); }
    // the preview playing now is fully rendered (tests: measure only once the background render is done)
    bool previewComplete() const { auto* p = current.load(); return p != nullptr && p->complete(); }
    const SampleInfo* currentSample() const { return wanted ? &*wanted : nullptr; }
    juce::String takeError();

    // The file to drop into the DAW: the matched render (written to Documents/Fetch Collections/Rendered)
    // or the original. Renders synchronously if needed.
    juce::File fileForDrag (const SampleInfo& s, juce::String& error);

    MatchPlan planFor (const SampleInfo& s) const;

    // host state, written by the audio thread
    std::atomic<double> hostBpm { 120.0 };
    std::atomic<bool> hostPlaying { false }, hostHasTempo { false };
    std::atomic<double> rate { 44100.0 };

    bool syncOn() const { return settings.getProperty ("sync", true); }
    juce::String projectKey() const { return settings.getProperty ("projectKey", "").toString(); }

private:
    // The last-used settings follow the user (Prefs.h): every change is written there, and they are put back
    // over a new instance's defaults and over the state a host restores from a project (like the Fetch
    // window, which also starts as last left). The project key belongs to the project.
    void valueTreePropertyChanged (juce::ValueTree&, const juce::Identifier&) override;
    void applyPrefs();
    bool applyingPrefs = false;

    void timerCallback() override;
    void run() override;   // render worker
    void install (std::shared_ptr<Preview> p, bool restart);

    // render requests (message thread -> worker)
    struct Request { SampleInfo info; MatchPlan plan; bool restart; int id; };
    juce::CriticalSection reqLock;
    std::optional<Request> pending;
    std::atomic<int> requestId { 0 };
    std::optional<SampleInfo> wanted;   // what the user asked to hear
    juce::String lastError;
    std::shared_ptr<bool> aliveFlag = std::make_shared<bool> (true);   // for callAsync after destruction

    // previews kept alive while the audio thread may still read them
    std::vector<std::shared_ptr<Preview>> alive;
    std::shared_ptr<Preview> shown;     // the one installed last
    std::atomic<Preview*> current { nullptr };
    std::atomic<Preview*> audioHolds { nullptr };   // the preview the audio thread saw last: not freed while
                                                    // it may still read it (a host can pause processing for long)
    juce::ThreadPool warmPool { 1 };
    std::atomic<int> warmId { 0 };
    std::atomic<int> trigger { 0 };
    std::atomic<bool> stopRequested { false }, active { false }, paused { false };
    std::atomic<double> seekTo { -1.0 }, playFrac { 0.0 }, playLen { 0.0 };
    juce::uint32 driftSince = 0;

    // audio thread state
    int lastTrigger = 0;
    Preview* lastPreview = nullptr;
    double pos = 0.0;
    bool wasSynced = false, playing = false, fadingOut = false, wasHostPlaying = false;
    std::atomic<bool> followHost { true };   // = the "Sync to host tempo" setting, for the audio thread
    std::atomic<bool> loopPreview { true };  // = the player menu's "Loop preview": off -> a loop plays once
    float fade = 0.0f;
    juce::SmoothedValue<float> gain;
    std::atomic<float>* volumeDb = nullptr;

    Renderer renderer;   // worker thread only

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (FetchProcessor)
};
