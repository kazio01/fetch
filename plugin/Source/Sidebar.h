#pragma once
#include <juce_gui_basics/juce_gui_basics.h>
#include "Theme.h"
#include "PluginProcessor.h"

// A pill-shaped filter chip (the Fetch window's .sh-chip).
class Chip : public juce::Button
{
public:
    Chip (const juce::String& text, bool on, std::function<void()> click, const juce::String& tip = {});
    void paintButton (juce::Graphics&, bool over, bool down) override;
    bool on;
};

// Tempo range with two handles; the full range = no filter.
class RangeSlider : public juce::Component, public juce::TooltipClient
{
public:
    RangeSlider (int lo, int hi);
    void paint (juce::Graphics&) override;
    void mouseDown (const juce::MouseEvent&) override;
    void mouseDrag (const juce::MouseEvent&) override;
    void mouseUp (const juce::MouseEvent&) override;
    void mouseDoubleClick (const juce::MouseEvent&) override;
    juce::String getTooltip() override { return tip; }
    void setTooltip (const juce::String&);
    bool isFull() const { return lo <= vmin && hi >= vmax; }
    juce::String text() const;
    void set (int l, int h) { lo = l; hi = h; repaint(); }
    int lo, hi;
    const int vmin, vmax;
    std::function<void()> onChange;   // after the drag ends

private:
    float xOf (int v) const;
    int valueAt (float x) const;
    int dragging = -1;   // 0 = lo, 1 = hi
    juce::String tip;
};

class Sidebar : public juce::Component, private juce::AsyncUpdater
{
public:
    explicit Sidebar (FetchProcessor&);
    ~Sidebar() override;

    void paint (juce::Graphics&) override;
    void paintOverChildren (juce::Graphics&) override;
    static constexpr int brandInset = 11, brandImage = 52, brandHeight = 65;   // logo area (see paint)
    void resized() override;

    void setCatalog (const juce::var& catalog);
    void setRecent (const juce::StringArray& r) { recent = r; triggerAsyncUpdate(); }
    void setView (const juce::String& v) { view = v; triggerAsyncUpdate(); }   // "ALL" or a folder path
    void setHostStatus (const juce::String& s);
    juce::String filterQuery() const;   // +loop inst:drums/kick genre:techno bpm:90-120 …
    juce::String genreLabel (const juce::String& id) const { return genreLabels->getProperty (id).toString(); }
    void clearFilters (bool notify = true);   // notify = false: no reload (a Similar list replaces the view)

    std::function<void()> onFiltersChanged;
    std::function<void (const juce::String& view)> onViewSelected;   // "ALL" or a folder path
    std::function<void (const juce::String& query)> onRecent;
    std::function<void()> onSyncChanged;      // tempo/key settings (re-render, re-filter)
    std::function<void (juce::Component& anchor)> onSettings;   // the gear in the header
    // Folders tree: the sidebar asks for one level; the editor answers with setFolders (off the message thread)
    std::function<void (const juce::String& parent)> onNeedFolders;
    void setFolders (const juce::String& parent, const juce::var& folders);
    void foldersChanged() { treeCache.clear(); triggerAsyncUpdate(); }   // a library folder was removed

private:
    struct Content;
    void rebuild();
    void changed();   // a filter changed: save, rebuild later, tell the editor
    void handleAsyncUpdate() override { rebuild(); }
    void saveState();
    void loadState();

    FetchProcessor& proc;
    juce::Viewport viewport;
    std::unique_ptr<juce::Button> gear;
    std::unique_ptr<Content> content;
    juce::var catalog;
    juce::DynamicObject::Ptr genreLabels = new juce::DynamicObject();
    juce::StringArray recent;
    juce::String view = "ALL";
    juce::String hostStatus;

    // filter state (as in the Fetch window)
    juce::String type;                        // loop / oneshot
    std::map<juce::String, juce::String> sound;   // feature group -> tag
    juce::StringArray instSel;                // [main, "kind:value", child]
    juce::String role;                        // chords / melody
    juce::StringArray genreSel;               // [group, genre]
    int bpmLo = 60, bpmHi = 200;
    juce::StringArray collapsed { "Genre", "Sound", "Tempo" };
    bool recentOpen = false;
    // Folders tree (as in the Fetch window): open/closed, opened folders, levels loaded from the service
    bool treeOpen = false;
    juce::StringArray treeExpanded;
    std::map<juce::String, juce::var> treeCache;   // parent ("" = roots) -> [{name, path, count}]
    juce::StringArray treePending;
    void treeRows (const juce::String& parent, int depth, std::function<void (const juce::var&, int)> add);

    friend struct Content;
    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (Sidebar)
};
