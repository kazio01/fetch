#include "FetchClient.h"

SampleInfo SampleInfo::fromJson (const juce::String& path, const juce::var& d)
{
    SampleInfo s;
    s.path = path;
    s.name = juce::File (path).getFileName();
    s.key = d["key"].toString();
    s.keyEst = (bool) d["key_est"];
    s.bpm = d["bpm"].isVoid() ? 0.0 : (double) d["bpm"];
    s.bpmEst = (bool) d["bpm_est"];
    s.dur = (double) d["dur"];
    s.lenMult = d["len"].isVoid() ? 0.0 : (double) d["len"];
    s.type = d["type"].toString();
    s.inst = d["inst_path"].toString();
    s.instLabel = d["inst_label"].toString();
    s.instEst = (bool) d["inst_est"];
    s.instId = d["inst"].toString();
    if (auto* t = d["itags"].getArray())
        for (auto& x : *t)
            s.itags.add (x.toString());
    if (auto* t = d["itags_est"].getArray())
        for (auto& x : *t)
            s.itagsEst.add (x.toString());
    s.pack = d["pack"].toString();
    s.liked = (bool) d["liked"];
    if (auto* g = d["genres"].getArray())
        for (auto& x : *g)
            s.genres.add (x.toString());
    return s;
}

FetchClient::FetchClient()
{
    auto p = juce::SystemStats::getEnvironmentVariable ("SAMPLEHAKU_HTTP_PORT", {}).getIntValue();
    if (p > 0 && p < 65536)
        port = p;
}

// True when the calling background job is being cancelled (the editor closing): long waits stop at once,
// so the editor's ThreadPool never has to kill a thread by force (that aborts the host).
static bool cancelled()
{
    if (auto* job = juce::ThreadPoolJob::getCurrentThreadPoolJob())
        return job->shouldExit();
    if (auto* t = juce::Thread::getCurrentThread())
        return t->threadShouldExit();
    return false;
}

juce::var FetchClient::post (const juce::String& endpoint, const juce::var& body, int timeoutMs, juce::String& error)
{
    juce::StreamingSocket sock;
    if (! sock.connect ("127.0.0.1", port, 1000))
    {
        error = "Fetch search service is not running";
        return {};
    }

    const auto payload = juce::JSON::toString (body, true).toStdString();
    juce::String head;
    head << "POST /v1/" << endpoint << " HTTP/1.1\r\n"
         << "Host: 127.0.0.1:" << port << "\r\n"
         << "X-Fetch-Client: vst3/" << FETCH_VERSION_STRING << "\r\n"
         << "Content-Type: application/json\r\n"
         << "Content-Length: " << (int) payload.size() << "\r\n"
         << "Connection: close\r\n\r\n";
    const auto request = head.toStdString() + payload;
    if (sock.write (request.data(), (int) request.size()) != (int) request.size())
    {
        error = "Could not send the request";
        return {};
    }

    juce::MemoryBlock reply;
    const auto deadline = juce::Time::getMillisecondCounter() + (juce::uint32) timeoutMs;
    char buf[16384];
    for (;;)
    {
        const auto left = (int) (deadline - juce::Time::getMillisecondCounter());
        if (left <= 0)
        {
            error = "The search service did not answer in time";
            return {};
        }
        if (cancelled())
        {
            error = "Cancelled";
            return {};
        }
        const int ready = sock.waitUntilReady (true, juce::jmin (left, 500));
        if (ready < 0)
            break;
        if (ready == 0)
            continue;
        const int n = sock.read (buf, (int) sizeof (buf), false);
        if (n <= 0)
            break;   // closed: the whole answer is here
        reply.append (buf, (size_t) n);
    }

    const auto text = reply.toString();
    const int split = text.indexOf ("\r\n\r\n");
    if (split < 0)
    {
        error = "Empty answer from the search service";
        return {};
    }
    juce::var result;
    const auto parsed = juce::JSON::parse (text.substring (split + 4), result);
    if (parsed.failed())
    {
        error = "Bad answer from the search service: " + parsed.getErrorMessage();
        return {};
    }
    if (result["error"].isString())
        error = result["error"].toString();
    return result;
}

bool FetchClient::ping (bool* ready, juce::String* serviceError)
{
    juce::String err;
    // 5 s: while the service loads a large index (Windows, cold disk, virus scan) a reply can lag
    auto r = post ("ping", new juce::DynamicObject(), 5000, err);
    const bool ok = err.isEmpty() && (bool) r["ok"];
    if (ready != nullptr)
        *ready = ok && (! r.hasProperty ("ready") || (bool) r["ready"]);
    if (serviceError != nullptr && ok && r["error"].isString())
        *serviceError = r["error"].toString();
    return ok;
}

juce::String FetchClient::waitReady (int timeoutMs)
{
    const auto deadline = juce::Time::getMillisecondCounter() + (juce::uint32) timeoutMs;
    juce::String err;
    for (;;)
    {
        bool ready = false;
        if (! ping (&ready, &err))
            break;                                  // gone: the caller's request reports it
        if (ready)
        {
            serviceLoading = false;
            return err.isNotEmpty() ? "The search service could not load the library: " + err : juce::String();
        }
        serviceLoading = true;
        if (cancelled())
            return "Cancelled";
        if (juce::Time::getMillisecondCounter() > deadline)
            break;
        juce::Thread::sleep (500);
    }
    serviceLoading = false;
    return {};
}

juce::String FetchClient::serverCommand() const
{
    // 1. environment, 2. a one-line file next to the plugin's settings, 3. the platform default
    auto cmd = juce::SystemStats::getEnvironmentVariable ("FETCH_SERVER_CMD", {});
    if (cmd.isEmpty())
    {
        auto f = juce::File::getSpecialLocation (juce::File::userApplicationDataDirectory)
                     .getChildFile ("Fetch").getChildFile ("server-command.txt");
        if (f.existsAsFile())
            cmd = f.loadFileAsString().trim();
    }
   #if JUCE_LINUX
    if (cmd.isEmpty())   // D-Bus activation starts hakupalvelu.py, which also serves HTTP
        cmd = "gdbus call --session --dest fi.samplehaku.Search --object-path /fi/samplehaku/SearchProvider "
              "--method fi.samplehaku.Search.Info /";
   #endif
    return cmd;
}

juce::String FetchClient::ensureRunning (int timeoutMs)
{
    if (ping())
        return waitReady (15 * 60 * 1000);

    const juce::ScopedLock sl (startLock);
    if (ping())
        return waitReady (15 * 60 * 1000);

    const auto cmd = serverCommand();
    if (cmd.isEmpty())
        return "Fetch search service is not running. Start it with: python fetchhttp.py "
               "(or put the command in " + juce::File::getSpecialLocation (juce::File::userApplicationDataDirectory)
                   .getChildFile ("Fetch").getChildFile ("server-command.txt").getFullPathName() + ")";

    const auto now = juce::Time::getMillisecondCounter();
    if (lastLaunchMs == 0 || now - lastLaunchMs > 30000)
    {
        lastLaunchMs = now;
        // Not waited for: gdbus returns when the service answers, a server command keeps running
        // (destroying a ChildProcess doesn't stop the process).
        launched = std::make_unique<juce::ChildProcess>();
        if (! launched->start (cmd, 0))
            return "Could not start the search service: " + cmd;
    }

    const auto deadline = juce::Time::getMillisecondCounter() + (juce::uint32) timeoutMs;
    while (juce::Time::getMillisecondCounter() < deadline)
    {
        if (ping())
            return waitReady (15 * 60 * 1000);
        if (cancelled())
            return "Cancelled";
        juce::Thread::sleep (300);
    }
    return "The search service did not start within " + juce::String (timeoutMs / 1000) + " s (port "
           + juce::String (port) + "). "
          #if JUCE_WINDOWS
           "Run \"Fetch Service (console).cmd\" in the Fetch folder to see why.";
          #else
           "Is Fetch installed (bash asenna_gnome.sh) and the library indexed?";
          #endif
}

PageResult FetchClient::toPage (const juce::var& reply, const juce::String& error)
{
    PageResult r;
    r.error = error;
    if (error.isNotEmpty())
        return r;
    r.total = reply.hasProperty ("total") ? (int) reply["total"] : reply["paths"].size();
    const auto details = reply["details"];
    if (auto* paths = reply["paths"].getArray())
        for (auto& p : *paths)
        {
            const auto path = p.toString();
            r.samples.add (SampleInfo::fromJson (path, details[juce::Identifier (path)]));
        }
    if (auto* sk = reply["skipped"].getArray())
        for (auto& s : *sk)
            r.skipped.add (s.toString());
    return r;
}

PageResult FetchClient::browse (const juce::String& text, const juce::String& filters, int offset, int limit,
                                const juce::String& order, int seed, const juce::String& under)
{
    if (auto e = ensureRunning(); e.isNotEmpty())
        return toPage ({}, e);
    auto* o = new juce::DynamicObject();
    o->setProperty ("text", text);
    o->setProperty ("filters", filters);
    o->setProperty ("offset", offset);
    o->setProperty ("limit", limit);
    o->setProperty ("order", order);
    o->setProperty ("seed", seed);
    if (under.isNotEmpty())
        o->setProperty ("folder", under);
    juce::String err;
    auto r = post ("browse", juce::var (o), 90000, err);
    return toPage (r, err);
}

juce::var FetchClient::addFolder (const juce::String& path, juce::String& error)
{
    if (auto e = ensureRunning(); e.isNotEmpty())
    {
        error = e;
        return {};
    }
    auto* o = new juce::DynamicObject();
    o->setProperty ("path", path);
    return post ("addfolder", juce::var (o), 30000, error);
}

juce::var FetchClient::removeFolder (const juce::String& path, juce::String& error)
{
    if (auto e = ensureRunning(); e.isNotEmpty())
    {
        error = e;
        return {};
    }
    auto* o = new juce::DynamicObject();
    o->setProperty ("path", path);
    auto r = post ("removefolder", juce::var (o), 120000, error);
    if (error.isEmpty() && r["error"].toString().isNotEmpty())
        error = r["error"].toString();
    return r;
}

juce::var FetchClient::indexStatus (juce::String& error)
{
    return post ("indexstatus", juce::var (new juce::DynamicObject()), 10000, error);
}

juce::var FetchClient::folders (const juce::String& parent, juce::String& error)
{
    if (auto e = ensureRunning(); e.isNotEmpty())
    {
        error = e;
        return {};
    }
    auto* o = new juce::DynamicObject();
    o->setProperty ("parent", parent);
    return post ("folders", juce::var (o), 60000, error);
}

PageResult FetchClient::folder (const juce::String& path, const juce::String& filters, const juce::String& order)
{
    if (auto e = ensureRunning(); e.isNotEmpty())
        return toPage ({}, e);
    auto* o = new juce::DynamicObject();
    o->setProperty ("path", path);
    o->setProperty ("filters", filters);
    o->setProperty ("order", order);
    juce::String err;
    auto r = post ("folder", juce::var (o), 60000, err);
    return toPage (r, err);
}

juce::var FetchClient::catalog (juce::String& error)
{
    if (error = ensureRunning(); error.isNotEmpty())
        return {};
    return post ("catalog", new juce::DynamicObject(), 120000, error);
}

juce::var FetchClient::like (const juce::StringArray& paths, int on, juce::String& error)
{
    auto* o = new juce::DynamicObject();
    juce::Array<juce::var> ps;
    for (auto& p : paths)
        ps.add (p);
    o->setProperty ("paths", ps);
    if (on >= 0)
        o->setProperty ("on", on == 1);
    return post ("like", juce::var (o), 10000, error);
}

juce::var FetchClient::setTags (const juce::StringArray& paths, const juce::var& tags, juce::String& error)
{
    auto* o = new juce::DynamicObject();
    juce::Array<juce::var> ps;
    for (auto& p : paths)
        ps.add (p);
    o->setProperty ("paths", ps);
    o->setProperty ("tags", tags);   // void -> null
    return post ("settags", juce::var (o), 120000, error);
}

juce::StringArray FetchClient::recent (const juce::String& add)
{
    auto* o = new juce::DynamicObject();
    if (add.isNotEmpty())
        o->setProperty ("add", add);
    juce::String err;
    auto r = post ("recent", juce::var (o), 5000, err);
    juce::StringArray out;
    if (auto* a = r["recent"].getArray())
        for (auto& q : *a)
            out.add (q.toString());
    return out;
}

PageResult FetchClient::similar (const juce::String& path, const juce::StringArray& conds, int n)
{
    if (auto e = ensureRunning(); e.isNotEmpty())
        return toPage ({}, e);
    auto* o = new juce::DynamicObject();
    o->setProperty ("path", path);
    juce::Array<juce::var> c;
    for (auto& s : conds)
        c.add (s);
    o->setProperty ("conds", c);
    o->setProperty ("n", n);
    juce::String err;
    auto r = post ("similar", juce::var (o), 60000, err);
    return toPage (r, err);
}
