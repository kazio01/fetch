#pragma once
#include <atomic>
#include <juce_core/juce_core.h>

// One sample as the search service describes it (hakupalvelu Engine.detail).
struct SampleInfo
{
    juce::String path, name, key, type, inst, instLabel, pack;
    juce::String instId;        // instrument node, e.g. "drums/hat/closed"
    juce::StringArray genres;   // genre ids
    juce::StringArray itags, itagsEst;   // style tags; those estimated from the sound
    bool keyEst = false, bpmEst = false, instEst = false, liked = false;
    double bpm = 0.0;   // 0 = unknown
    double dur = 0.0;
    double lenMult = 0.0;   // loop counted at bpm · lenMult (player menu /2, x2; saved as "len"); 0 = automatic

    bool isLoop() const { return type == "Loop"; }
    static SampleInfo fromJson (const juce::String& path, const juce::var& d);
};

struct PageResult
{
    juce::String error;
    int total = 0;
    juce::Array<SampleInfo> samples;
    juce::StringArray skipped;   // Similar: conditions unknown for the source file
};

// Talks to the Fetch search service's JSON API on 127.0.0.1 (fetchhttp.py).
// Blocking calls: use them from a background thread, never the audio or message thread.
// A sample path from the service as a file here. The Windows plugin running under Wine on Linux talks to
// the Linux service (same 127.0.0.1), which gives Linux paths ("/home/me/x.wav"): Wine shows "/" as drive Z:.
// On real Windows the service's paths start with a drive letter or "//server", and nothing changes.
inline juce::File localFile (const juce::String& path)
{
   #if JUCE_WINDOWS
    if (path.startsWithChar ('/') && ! path.startsWith ("//"))
        return juce::File ("Z:" + path.replaceCharacter ('/', '\\'));
   #endif
    return juce::File (path);
}

// A file as an item of an external drag. Linux (XDND text/uri-list): a %-escaped file:// URI. JUCE would send
// "file://" + the raw path, and REAPER (SWELL) cuts the name at the first space, so most library paths
// ("Sample Collection", "Fetch Collections") arrived as a missing file. Elsewhere the plain path.
inline juce::String dragItem (const juce::File& f)
{
   #if JUCE_LINUX
    return juce::URL (f).toString (false);
   #else
    return f.getFullPathName();
   #endif
}

class FetchClient
{
public:
    FetchClient();

    // Makes sure the service runs: on Linux D-Bus activation starts it, on Windows (or with
    // FETCH_SERVER_CMD set) the configured command. Waits up to timeoutMs. Returns "" or an error.
    // 120 s: a cold start on Windows (Python imports scanned by Defender, a 100k-sample index) measured
    // well over the old 20 s; the plugin gave up while the service was still starting.
    juce::String ensureRunning (int timeoutMs = 120000);

    PageResult browse (const juce::String& text, const juce::String& filters, int offset, int limit,
                       const juce::String& order = "path", int seed = 0, const juce::String& under = {});
    // The library folder tree one level at a time: parent "" = the roots -> {folders: [{name, path, count}], files}
    juce::var folders (const juce::String& parent, juce::String& error);
    // Settings > Library folders: add one (analysed in the background), the indexer's progress
    juce::var addFolder (const juce::String& path, juce::String& error);
    // Remove folder: out of the library and the index only (files on disk stay) -> {removed, folder} or {error}
    juce::var removeFolder (const juce::String& path, juce::String& error);
    juce::var indexStatus (juce::String& error);
    PageResult folder (const juce::String& path, const juce::String& filters, const juce::String& order);
    PageResult similar (const juce::String& path, const juce::StringArray& conds, int n);
    juce::var catalog (juce::String& error);
    // like: on = 1 like, 0 unlike, -1 toggle -> {path: liked}
    juce::var like (const juce::StringArray& paths, int on, juce::String& error);
    juce::StringArray recent (const juce::String& add = {});
    // Edit tags: tags = {type?, inst?, tags?} or void (= back to automatic) -> {path: details}
    juce::var setTags (const juce::StringArray& paths, const juce::var& tags, juce::String& error);

    int getPort() const { return port; }

    // True while the service answers but is still loading the library (ping: ready = false).
    // A cold start after an update rebuilds the index caches: ~2 min for 100k samples on Windows.
    std::atomic<bool> serviceLoading { false };

private:
    juce::var post (const juce::String& endpoint, const juce::var& body, int timeoutMs, juce::String& error);
    bool ping (bool* ready = nullptr, juce::String* serviceError = nullptr);
    juce::String waitReady (int timeoutMs);
    juce::String serverCommand() const;
    PageResult toPage (const juce::var& reply, const juce::String& error);

    int port = 47811;
    juce::CriticalSection startLock;
    juce::uint32 lastLaunchMs = 0;
    std::unique_ptr<juce::ChildProcess> launched;
};
