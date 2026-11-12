#pragma once
#include <atomic>
#include <juce_audio_basics/juce_audio_basics.h>
#include <juce_audio_formats/juce_audio_formats.h>
#include "FetchClient.h"

// Musical keys as the search service writes them: "C", "C#", "Eb", … plus "m" for minor.
namespace keys
{
    // -> {pitch class 0–11, minor?}; pc = -1 if unknown
    std::pair<int, bool> parse (const juce::String& key);
    juce::String name (int pc, bool minor);
    // Shortest transposition (−6…+5 semitones) that moves a sample in `from` to the project key `to`.
    // Major↔minor goes to the relative key (Am sample in a C major project: 0).
    int semitonesBetween (const juce::String& from, const juce::String& to);
    juce::StringArray all();   // "C", "Cm", "C#", "C#m", …
}

// What the plugin should do to a sample for the current project.
struct MatchPlan
{
    bool tempo = false;        // stretched to host tempo
    double sampleBpm = 0.0;    // the sample's tempo after half/double correction
    double hostBpm = 120.0;
    double beats = 0.0;        // loop length in beats (whole beats when close)
    int semitones = 0;         // key shift
    double targetSampleRate = 44100.0;

    bool changesAudio() const { return tempo || semitones != 0; }
    juce::String suffix() const;   // "128 BPM, Am" for file names
};

MatchPlan planFor (const SampleInfo& s, double hostBpm, double sampleRate,
                   bool syncTempo, const juce::String& projectKey);

// A sample rendered for playback: stereo, at the host's sample rate.
struct Preview
{
    SampleInfo info;
    MatchPlan plan;
    juce::AudioBuffer<float> audio;
    bool loop = false;           // repeats while previewing
    bool synced = false;         // phase follows the host's beat position
    juce::uint32 retiredAt = 0;  // message thread bookkeeping
    // Frames of `audio` rendered so far (loops are stretched in chunks and may start playing before the
    // rest is done). The audio thread plays only below this.
    std::atomic<int> ready { 0 };
    bool complete() const { return ready.load() >= audio.getNumSamples(); }
};

// Loads, resamples and stretches. Thread-safe (create one per thread or guard externally).
class Renderer
{
public:
    Renderer();
    // Returns nullptr and sets error on failure. shouldExit lets a newer request cancel this one.
    // onPlayable: called (on this thread) as soon as the start can play while the rest renders on; the
    // same Preview is returned when it is complete. Without it, render() returns only finished previews.
    std::shared_ptr<Preview> render (const SampleInfo& s, const MatchPlan& plan, juce::String& error,
                                     const std::function<bool()>& shouldExit = {},
                                     const std::function<void (std::shared_ptr<Preview>)>& onPlayable = {});
    // Writes a preview as a 24-bit WAV into renderFolder(); returns the file.
    static juce::File writeWav (const Preview& p, juce::String& error);
    static juce::File renderFolder();   // ~/Documents/Fetch Collections/Rendered

private:
    juce::AudioFormatManager formats;
};
