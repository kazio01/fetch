#pragma once
#include <juce_core/juce_core.h>

// Settings that follow the user rather than one DAW project: which sidebar sections are open and the
// Host sync switches. Every Fetch instance starts from these (a saved project still restores its own
// switches); the Fetch window keeps its layout the same way in ikkuna.json.
// Linux: ~/.config/samplehaku/plugin.json, Windows: %APPDATA%\Fetch\plugin.json. Message thread only.
namespace prefs
{
inline juce::File file()
{
#if JUCE_LINUX
    auto cfg = juce::SystemStats::getEnvironmentVariable ("XDG_CONFIG_HOME", {});
    const auto base = cfg.isNotEmpty() ? juce::File (cfg) : juce::File ("~/.config");
    return base.getChildFile ("samplehaku/plugin.json");
#else
    return juce::File::getSpecialLocation (juce::File::userApplicationDataDirectory).getChildFile ("Fetch/plugin.json");
#endif
}

inline juce::var& cache()
{
    static juce::var v = [] {
        if (juce::SystemStats::getEnvironmentVariable ("FETCH_NO_PREFS", {}).isNotEmpty())   // tests
            return juce::var (new juce::DynamicObject());
        auto parsed = juce::JSON::parse (file());
        return parsed.isObject() ? parsed : juce::var (new juce::DynamicObject());
    }();
    return v;
}

inline juce::var get (const juce::Identifier& key, const juce::var& def = {})
{
    auto* o = cache().getDynamicObject();
    return o->hasProperty (key) ? o->getProperty (key) : def;
}

inline void set (const juce::Identifier& key, const juce::var& value)
{
    auto* o = cache().getDynamicObject();
    if (o->hasProperty (key) && o->getProperty (key) == value)
        return;
    o->setProperty (key, value);
    if (juce::SystemStats::getEnvironmentVariable ("FETCH_NO_PREFS", {}).isNotEmpty())
        return;
    const auto f = file();
    f.getParentDirectory().createDirectory();
    f.replaceWithText (juce::JSON::toString (cache()));
}
inline void saveAppearance (float ui, float font)
{
    const auto f = file().getSiblingFile ("appearance.json");
    auto v = juce::JSON::parse (f);
    if (! v.isObject())
        v = juce::var (new juce::DynamicObject());
    v.getDynamicObject()->setProperty ("ui_scale", juce::roundToInt (ui * 100) / 100.0);
    v.getDynamicObject()->setProperty ("font_scale", juce::roundToInt (font * 100) / 100.0);
    f.getParentDirectory().createDirectory();
    f.replaceWithText (juce::JSON::toString (v));
}

// Appearance (UI scale, text size): written by the Fetch window's Settings, next to plugin.json.
// Returns true when the values changed since the last call.
inline bool loadAppearance (float& ui, float& font)
{
    const auto v = juce::JSON::parse (file().getSiblingFile ("appearance.json"));
    auto clamp = [] (const juce::var& x) { return x.isVoid() ? 1.0f : juce::jlimit (0.75f, 1.5f, (float) (double) x); };
    const float u = clamp (v["ui_scale"]), f = clamp (v["font_scale"]);
    const bool changed = ! juce::approximatelyEqual (u, ui) || ! juce::approximatelyEqual (f, font);
    ui = u;
    font = f;
    return changed;
}
} // namespace prefs
