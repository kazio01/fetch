#include "Sidebar.h"
#include "Preview.h"

// ---------------------------------------------------------------- small widgets

Chip::Chip (const juce::String& label, bool initiallyOn, std::function<void()> click, const juce::String& tip)
    : juce::Button (label), on (initiallyOn)
{
    onClick = std::move (click);
    setTooltip (tip.isNotEmpty() ? tip : label);
    setWantsKeyboardFocus (false);
    setMouseCursor (juce::MouseCursor::PointingHandCursor);
}

void Chip::paintButton (juce::Graphics& g, bool over, bool down)
{
    auto r = getLocalBounds().toFloat().reduced (0.5f);
    g.setColour (on ? theme::accentBg : down ? theme::buttonPressed : over ? theme::buttonHover : theme::buttonBg);
    g.fillRoundedRectangle (r, r.getHeight() / 2);
    g.setColour (on ? juce::Colours::white : theme::fg);
    float size = theme::small;   // .sh-chip: 0.9em, regular like the rest of the window
    auto f = theme::font (size);
    while (size > 10.5f && juce::GlyphArrangement::getStringWidth (f, getButtonText()) > r.getWidth() - 8)
        f = theme::font (size -= 0.5f);   // .sh-tight
    g.setFont (f);
    g.drawFittedText (getButtonText(), getLocalBounds().reduced (4, 0), juce::Justification::centred, 1, 0.9f);
}

RangeSlider::RangeSlider (int l, int h) : lo (l), hi (h), vmin (l), vmax (h)
{
    setTooltip ("Drag the ends to set a BPM range; double-click resets");
}

void RangeSlider::setTooltip (const juce::String& t) { tip = t; }

float RangeSlider::xOf (int v) const
{
    return 10.0f + (float) (v - vmin) / (float) (vmax - vmin) * ((float) getWidth() - 20.0f);
}

int RangeSlider::valueAt (float x) const
{
    const float t = juce::jlimit (0.0f, 1.0f, (x - 10.0f) / ((float) getWidth() - 20.0f));
    return vmin + juce::roundToInt (t * (float) (vmax - vmin));
}

juce::String RangeSlider::text() const
{
    if (isFull())
        return {};
    if (lo <= vmin)
        return juce::String::fromUTF8 ("\xe2\x89\xa4 ") + juce::String (hi);   // ≤ 120
    if (hi >= vmax)
        return juce::String (lo) + "+";
    return juce::String (lo) + juce::String::fromUTF8 ("\xe2\x80\x93") + juce::String (hi);
}

void RangeSlider::paint (juce::Graphics& g)
{
    const float cy = 12.0f;
    g.setColour (theme::trough);
    g.fillRoundedRectangle (10.0f, cy - 3.0f, (float) getWidth() - 20.0f, 6.0f, 3.0f);
    g.setColour (theme::accentBg);
    g.fillRoundedRectangle (xOf (lo), cy - 3.0f, xOf (hi) - xOf (lo), 6.0f, 3.0f);
    for (int v : { lo, hi })
    {
        g.setColour (juce::Colour (0x33000000));
        g.fillEllipse (xOf (v) - 8.0f, cy - 7.0f, 16.0f, 16.0f);
        g.setColour (theme::knob);
        g.fillEllipse (xOf (v) - 8.0f, cy - 8.0f, 16.0f, 16.0f);
    }
    g.setFont (theme::font (12.0f));
    g.setColour (theme::dim);
    g.drawText (lo <= vmin ? "Any" : juce::String (lo), 4, 22, 60, 14, juce::Justification::centredLeft);
    g.drawText (hi >= vmax ? "Any" : juce::String (hi), getWidth() - 64, 22, 60, 14, juce::Justification::centredRight);
}

void RangeSlider::mouseDown (const juce::MouseEvent& e)
{
    const float x = (float) e.x;
    dragging = std::abs (x - xOf (lo)) <= std::abs (x - xOf (hi)) ? 0 : 1;
    if (lo == hi)
        dragging = x > xOf (hi) ? 1 : 0;
    mouseDrag (e);
}

void RangeSlider::mouseDrag (const juce::MouseEvent& e)
{
    const int v = valueAt ((float) e.x);
    if (dragging == 0)
        lo = juce::jmin (v, hi);
    else if (dragging == 1)
        hi = juce::jmax (v, lo);
    repaint();
}

void RangeSlider::mouseUp (const juce::MouseEvent&)
{
    dragging = -1;
    if (onChange)
        onChange();
}

void RangeSlider::mouseDoubleClick (const juce::MouseEvent&)
{
    lo = vmin;
    hi = vmax;
    repaint();
    if (onChange)
        onChange();
}

namespace
{
// Bold dim title ("Collections", "Filters") with an optional button at the right.
struct Title : juce::Component
{
    Title (const juce::String& t, const juce::String& right = {}) : text (t), rightText (right) { setInterceptsMouseClicks (false, true); }
    void paint (juce::Graphics& g) override
    {
        g.setColour (theme::fg.withMultipliedAlpha (0.6f));   // .sh-sidebar-title: 0.85em bold, 60 %
        g.setFont (theme::font (12.5f, true));
        g.drawText (text, getLocalBounds().withTrimmedLeft (12).withTrimmedBottom (4), juce::Justification::bottomLeft);
        if (rightText.isNotEmpty())
        {
            g.setColour (theme::accent);
            g.setFont (theme::font (12.0f));
            g.drawText (rightText, getLocalBounds().withTrimmedRight (12).withTrimmedBottom (2), juce::Justification::bottomRight);
        }
    }
    juce::String text, rightText;
};

// A navigation-sidebar row: icon + label, rounded highlight when selected.
struct SideRow : juce::Button
{
    SideRow (const juce::String& t, juce::Path i, bool sel, std::function<void()> click, juce::Colour ic = {})
        : juce::Button (t), icon (std::move (i)), selected (sel), iconColour (ic)
    {
        onClick = std::move (click);
        setTooltip (t);
        setWantsKeyboardFocus (false);
    }
    void paintButton (juce::Graphics& g, bool over, bool down) override
    {
        auto r = getLocalBounds().toFloat().reduced (6.0f, 1.0f);
        if (selected || over || down)
        {
            g.setColour (selected ? theme::sideSelected : theme::hover);
            g.fillRoundedRectangle (r, 6.0f);
        }
        theme::drawIcon (g, icon, { 14.0f, r.getCentreY() - 8.0f, 16.0f, 16.0f }, iconColour == juce::Colour() ? theme::fg : iconColour,
                         filledIcon, 1.5f);
        g.setColour (theme::fg);
        g.setFont (theme::font());
        g.drawText (getButtonText(), r.toNearestInt().withTrimmedLeft (34), juce::Justification::centredLeft, true);
    }
    juce::Path icon;
    bool selected, filledIcon = false;
    juce::Colour iconColour;
};

// The settings gear at the header's top right (the window's emblem-system-symbolic, flat circular button).
struct GearButton : juce::Button
{
    GearButton() : juce::Button ("Settings") { setTooltip ("Settings"); setWantsKeyboardFocus (false); }
    void paintButton (juce::Graphics& g, bool over, bool down) override
    {
        auto r = getLocalBounds().toFloat();
        if (over || down)
        {
            g.setColour (down ? theme::buttonPressed : theme::hover);
            g.fillEllipse (r);
        }
        const auto c = r.getCentre();
        juce::Path p;   // 8 teeth around a ring, 16 px box
        const int teeth = 8;
        for (int i = 0; i < teeth * 2; ++i)
        {
            const float a0 = juce::MathConstants<float>::twoPi * (float) i / (teeth * 2) - 0.12f;
            const float a1 = juce::MathConstants<float>::twoPi * (float) (i + 1) / (teeth * 2) + 0.12f;
            const float rad = (i % 2 == 0) ? 7.5f : 5.6f;
            for (float a : { a0, a1 })
            {
                const juce::Point<float> pt (c.x + rad * std::sin (a), c.y - rad * std::cos (a));
                if (i == 0 && a == a0)
                    p.startNewSubPath (pt);
                else
                    p.lineTo (pt);
            }
        }
        p.closeSubPath();
        p.addEllipse (c.x - 2.6f, c.y - 2.6f, 5.2f, 5.2f);
        p.setUsingNonZeroWinding (false);
        g.setColour (theme::fg);
        g.fillPath (p);
    }
};

// "Folders" under All samples: a SideRow with an open/closed chevron at the right edge.
struct FoldersHead : SideRow
{
    FoldersHead (bool open, std::function<void()> click)
        : SideRow ("Folders", theme::folder(), false, std::move (click)), isOpen (open)
    {
        setTooltip ("Browse the library folder by folder");
    }
    void paintButton (juce::Graphics& g, bool over, bool down) override
    {
        SideRow::paintButton (g, over, down);
        theme::drawIcon (g, isOpen ? theme::chevronDown() : theme::chevronRight(),
                         { (float) getWidth() - 28.0f, getHeight() / 2.0f - 6.0f, 12.0f, 12.0f }, theme::fg, false, 2.0f);
    }
    bool isOpen;
};

// A folder in the Folders tree: indent, chevron (opens subfolders), folder icon, name.
// A click on the chevron opens/closes the folder, elsewhere shows its samples.
struct TreeRow : juce::Button
{
    TreeRow (const juce::String& name, int d, bool expanded, bool leaf, bool sel,
             std::function<void()> toggle, std::function<void()> open)
        : juce::Button (name), depth (d), isOpen (expanded), noChildren (leaf), selected (sel),
          onToggle (std::move (toggle)), onOpen (std::move (open))
    {
        setWantsKeyboardFocus (false);
    }
    float arrowX() const { return 13.0f + 12.0f * (float) (depth - 1); }   // left edge of the 24 px arrow button
    void clicked (const juce::ModifierKeys&) override
    {
        const auto x = (float) getMouseXYRelative().x;
        if (! noChildren && x >= arrowX() && x < arrowX() + 24.0f)
            onToggle();
        else
            onOpen();
    }
    void paintButton (juce::Graphics& g, bool over, bool down) override
    {
        auto r = getLocalBounds().toFloat().reduced (6.0f, 1.0f);
        if (selected || over || down)
        {
            g.setColour (selected ? theme::sideSelected : theme::hover);
            g.fillRoundedRectangle (r, 6.0f);
        }
        const float ax = arrowX(), cy = r.getCentreY();
        if (! noChildren)
            theme::drawIcon (g, isOpen ? theme::chevronDown() : theme::chevronRight(),
                             { ax + 6.0f, cy - 6.0f, 12.0f, 12.0f }, theme::fg, false, 2.0f);
        theme::drawIcon (g, theme::folder(), { ax + 28.0f, cy - 8.0f, 16.0f, 16.0f }, theme::fg, false, 1.5f);
        g.setColour (theme::fg);
        g.setFont (theme::font());
        const int tx = (int) (ax + 48.0f);
        g.drawText (getButtonText(), tx, (int) r.getY(), (int) r.getRight() - 6 - tx, (int) r.getHeight(),
                    juce::Justification::centredLeft, true);
    }
    int depth;
    bool isOpen, noChildren, selected;
    std::function<void()> onToggle, onOpen;
};

// A collapsible group header: arrow, name, summary of what is chosen.
struct SectionHead : juce::Button
{
    SectionHead (const juce::String& t, const juce::String& s, bool open, std::function<void()> click)
        : juce::Button (t), summary (s), isOpen (open)
    {
        onClick = std::move (click);
        setWantsKeyboardFocus (false);
    }
    void paintButton (juce::Graphics& g, bool over, bool) override
    {
        auto r = getLocalBounds().toFloat().reduced (indent - 6.0f, 2.0f).withTrimmedRight (12.0f - indent + 6.0f);
        if (over)
        {
            g.setColour (theme::hover);
            g.fillRoundedRectangle (r, 6.0f);
        }
        // as in the Fetch window: arrow, the name (0.8em bold, 55 %), what is chosen (accent, right)
        theme::drawIcon (g, isOpen ? theme::chevronDown() : theme::chevronRight(),
                         { indent + 2.0f, r.getCentreY() - 6.0f, 12.0f, 12.0f }, theme::fg, false, 2.0f);
        // filter groups: full ink, normal weight, the sidebar titles' size; Recent searches as the titles
        g.setFont (theme::font (12.5f, big));
        g.setColour (big ? theme::fg.withMultipliedAlpha (0.6f) : theme::fg);
        const auto name = getButtonText();
        const float nx = indent + 20.0f;
        const float nw = juce::GlyphArrangement::getStringWidth (g.getCurrentFont(), name) + 10.0f;
        g.drawText (name, juce::Rectangle<float> (nx, r.getY(), nw, r.getHeight()).toNearestInt(), juce::Justification::centredLeft);
        g.setFont (theme::font (11.7f));
        g.setColour (theme::accent);
        g.drawText (summary, juce::Rectangle<float> (nx + nw, r.getY(), r.getRight() - 6.0f - nx - nw, r.getHeight()).toNearestInt(),
                    juce::Justification::centredRight, true);
    }
    juce::String summary;
    bool isOpen;
    float indent = 24.0f;   // filter groups; Recent searches sits at the sidebar title's edge
    bool big = false;
};

// Indented sub level with an accent line at the left (.sh-sublevel)
struct SubLevel : juce::Component
{
    explicit SubLevel (const juce::String& t) : title (t) { setInterceptsMouseClicks (false, true); }
    void paint (juce::Graphics& g) override
    {
        g.setColour (theme::accentBg.withAlpha (0.5f));
        g.fillRect (0, 0, 2, getHeight());
        g.setColour (theme::fg.withMultipliedAlpha (0.55f));
        g.setFont (theme::font (11.7f));
        g.drawText (title + juce::String::fromUTF8 (" \xe2\x80\xba"), 7, 0, getWidth() - 7, 16, juce::Justification::centredLeft);
    }
    juce::String title;
};
} // namespace

// ---------------------------------------------------------------- content

struct Sidebar::Content : juce::Component
{
    explicit Content (Sidebar& s) : sb (s)
    {
        auto& st = sb.proc.settings;
        auto setupSwitch = [this, &st] (juce::ToggleButton& b, const char* prop, bool def, const char* tip, bool refilter) {
            b.setToggleState ((bool) st.getProperty (prop, def), juce::dontSendNotification);
            b.setTooltip (tip);
            b.setWantsKeyboardFocus (false);
            b.onClick = [this, &b, prop, refilter] {
                sb.proc.settings.setProperty (prop, b.getToggleState(), nullptr);
                sb.proc.settingsChanged();
                if (sb.onSyncChanged)
                    sb.onSyncChanged();
                if (refilter && sb.onFiltersChanged)
                    sb.onFiltersChanged();
            };
            addAndMakeVisible (b);
        };
        setupSwitch (sync, "sync", true,
                     "Loops with a known tempo are time-stretched to the host tempo and, while the transport runs, "
                     "play in phase with the arrangement (as if started at bar 1).", false);
        setupSwitch (onlyTempo, "onlyTempo", false, "Only loops at the host tempo (+/- 3 %, also half/double tempo).", true);
        setupSwitch (onlyKey, "onlyKey", false, "Only samples in the project key or a Camelot neighbour.", true);
        setupSwitch (dragMatched, "dragMatched", true,
                     "Dragging rows into the DAW drops copies rendered at the host tempo and project key "
                     "(Documents/Fetch Collections/Rendered). Off: the original files.", false);

        keyBox.addItem ("Off", 1);
        const auto names = keys::all();
        for (int i = 0; i < names.size(); ++i)
            keyBox.addItem (names[i], i + 2);
        const int k = names.indexOf (sb.proc.projectKey());
        keyBox.setSelectedId (k >= 0 ? k + 2 : 1, juce::dontSendNotification);
        keyBox.setTooltip ("Tonal samples are transposed to this key (the nearest way, at most 6 semitones). "
                           "VST3 hosts don't report the project key, so pick it here.");
        keyBox.setWantsKeyboardFocus (false);
        keyBox.onChange = [this] {
            const int id = keyBox.getSelectedId();
            sb.proc.settings.setProperty ("projectKey", id >= 2 ? keys::all()[id - 2] : juce::String(), nullptr);
            sb.proc.settingsChanged();
            if (sb.onSyncChanged)
                sb.onSyncChanged();
            if (onlyKey.getToggleState() && sb.onFiltersChanged)
                sb.onFiltersChanged();
        };
        addAndMakeVisible (keyBox);

        tempo.onChange = [this] {
            sb.bpmLo = tempo.lo;
            sb.bpmHi = tempo.hi;
            sb.changed();
        };
        addChildComponent (tempo);

        clear.setButtonText ("Clear");
        clear.getProperties().set ("flat", true);
        clear.getProperties().set ("fontSize", 12.0f);
        clear.setTooltip ("Clear all filters");
        clear.setWantsKeyboardFocus (false);
        clear.onClick = [this] { sb.clearFilters(); };
        addChildComponent (clear);
    }

    // Lays everything out for width w; returns the height. Measurements from the Fetch window.
    int build (int w)
    {
        items.clear();
        int y = 0;
        auto add = [&] (juce::Component* c, int h, int indent = 0) {
            items.add (c);
            addAndMakeVisible (c);
            c->setBounds (indent, y, w - indent, h);
            y += h;
        };
        auto title = [&] (const juce::String& t) { add (new Title (t), 34); };

        // Collections
        y += 6;
        title ("Collections");
        add (new SideRow ("All samples", theme::list(), sb.view == "ALL", [this] { pick ("ALL"); }), 38);
        add (new FoldersHead (sb.treeOpen, [this] {
            sb.treeOpen = ! sb.treeOpen;
            if (sb.treeOpen)
                sb.treeCache.clear();          // fresh from the service (folders may have been added)
            sb.saveState();
            sb.triggerAsyncUpdate();
        }), 38);
        if (sb.treeOpen)
            sb.treeRows ({}, 1, [&] (const juce::var& d, int depth) {
                const auto path = d["path"].toString();
                const auto it = sb.treeCache.find (path);
                const bool leaf = (d.hasProperty ("subs") && ! (bool) d["subs"])
                                  || (it != sb.treeCache.end() && it->second.size() == 0);   // the deepest folder: no arrow
                auto* r = new TreeRow (d["name"].toString(), depth, sb.treeExpanded.contains (path), leaf,
                                       sb.view == "d:" + path,
                                       [this, path] {
                                           if (sb.treeExpanded.contains (path))
                                               sb.treeExpanded.removeString (path);
                                           else
                                               sb.treeExpanded.add (path);
                                           sb.saveState();
                                           sb.triggerAsyncUpdate();
                                       },
                                       [this, path] { pick ("d:" + path); });
                r->setTooltip (d["name"].toString() + juce::String::fromUTF8 (" \xc2\xb7 ") + juce::String ((int) d["count"]) + " samples");
                add (r, 38);
            });
        if (auto* cs = sb.catalog["collections"].getArray())
            for (auto& c : *cs)
            {
                const auto path = c["path"].toString();
                const bool liked = c["liked"];
                auto* r = new SideRow (c["name"].toString(), liked ? theme::heart() : theme::folder(), sb.view == path,
                                       [this, path] { pick (path); }, liked ? theme::liked : juce::Colour());
                r->filledIcon = liked;
                add (r, 38);
            }

        // Filters
        clear.setVisible (sb.filterQuery().isNotEmpty());
        add (new Title ("Filters"), 34);
        clear.setBounds (w - 70, y - 28, 58, 24);

        section ("Type", typeSummary(), y, w, [&] {
            juce::Array<ChipSpec> chips;
            if (auto* ts = sb.catalog["types"].getArray())
                for (auto& t : *ts)
                {
                    const auto name = t["name"].toString();
                    chips.add ({ t["label"].toString(), sb.type == name, [this, name] {
                                     sb.type = sb.type == name ? juce::String() : name;
                                     sb.changed();
                                 }, "+" + name });
                }
            flow (chips, y, w, juce::jmax (1, chips.size()), 0);   // Loop / One-shot share the full width
        });
        section ("Instrument", instSummary(), y, w, [&] { instruments (y, w); });
        section ("Genre", genreSummary(), y, w, [&] { genres (y, w); });
        section ("Sound", soundSummary(), y, w, [&] {
            juce::Array<ChipSpec> chips;
            if (auto* ss = sb.catalog["sound"].getArray())
                for (auto& s : *ss)
                {
                    const auto name = s["name"].toString(), group = s["group"].toString();
                    const bool on = sb.sound.count (group) && sb.sound[group] == name;
                    chips.add ({ name.substring (0, 1).toUpperCase() + name.substring (1), on, [this, name, group, on] {
                                     if (on)
                                         sb.sound.erase (group);
                                     else
                                         sb.sound[group] = name;   // the opposite in the same group goes off
                                     sb.changed();
                                 }, "+" + name });
                }
            flow (chips, y, w, 3, 0);
        });
        tempo.setVisible (false);
        section ("Tempo", tempo.text(), y, w, [&] {
            tempo.set (sb.bpmLo, sb.bpmHi);
            tempo.setVisible (true);
            tempo.setBounds (16, y + 2, w - 32, 38);
            y += 42;
        });

        // Host sync (the plugin's own group): between Tempo and Recent searches
        const bool syncOpen = ! sb.collapsed.contains ("Host sync");
        for (auto* c : std::initializer_list<juce::Component*> { &sync, &onlyTempo, &onlyKey, &dragMatched, &keyBox })
            c->setVisible (syncOpen);
        section ("Host sync", sb.hostStatus, y, w, [&] {
            const int left = 26, right = w - 23;
            for (auto* b : { &sync, &onlyTempo, &onlyKey, &dragMatched })
            {
                b->setBounds (left, y, right - left, 22);
                y += 22;
            }
            keyBox.setBounds (right - 56, y + 1, 56, 20);   // "Project key" is painted at its left
            y += 24;
        });

        // Recent searches
        if (! sb.recent.isEmpty())
        {
            y += 10;
            auto* h = new SectionHead ("Recent searches", {}, sb.recentOpen, [this] {
                sb.recentOpen = ! sb.recentOpen;
                sb.saveState();
                sb.triggerAsyncUpdate();
            });
            h->indent = 12.0f;
            h->big = true;
            add (h, 32);
            if (sb.recentOpen)
                for (int i = 0; i < juce::jmin (20, sb.recent.size()); ++i)
                {
                    const auto q = sb.recent[i];
                    add (new SideRow (q, theme::search(), false, [this, q] {
                             if (sb.onRecent)
                                 sb.onRecent (q);
                         }, theme::dim), 36);
                }
        }
        return y + 12;
    }

    void section (const juce::String& name, const juce::String& summary, int& y, int w, const std::function<void()>& body)
    {
        const bool open = ! sb.collapsed.contains (name);
        auto* h = new SectionHead (name, summary, open, [this, name] {
            if (sb.collapsed.contains (name))
                sb.collapsed.removeString (name);
            else
                sb.collapsed.add (name);
            sb.saveState();
            sb.triggerAsyncUpdate();
        });
        items.add (h);
        addAndMakeVisible (h);
        h->setBounds (0, y, w, 32);
        y += 32;
        if (open)
        {
            y += 2;
            body();
            y += 4;
        }
    }

    using ChipSpec = std::tuple<juce::String, bool, std::function<void()>, juce::String>;

    void flow (const juce::Array<ChipSpec>& chips, int& y, int w, int cols, int indent)
    {
        const int left = 23 + indent, gap = 10, ch = 24, step = 34;
        const int cw = (w - left - 23 - gap * (cols - 1)) / cols;
        for (int i = 0; i < chips.size(); ++i)
        {
            auto& [label, on, click, tip] = chips.getReference (i);
            auto* c = new Chip (label, on, click, tip);
            items.add (c);
            addAndMakeVisible (c);
            c->setBounds (left + (i % cols) * (cw + gap), y + (i / cols) * step, cw, ch);
        }
        y += ((chips.size() + cols - 1) / cols) * step - (step - ch);
    }

    void sublevel (const juce::String& t, const juce::Array<ChipSpec>& chips, int& y, int w, int depth, int cols = 3)
    {
        const int indent = 9 * depth;
        auto* s = new SubLevel (t);
        items.add (s);
        addAndMakeVisible (s);
        const int top = y;
        y += 20;
        flow (chips, y, w, cols, indent + 7);
        s->setBounds (23 + indent - 2, top, w - 23 - indent, y - top);
        y += 6;
    }

    void instruments (int& y, int w)
    {
        juce::Array<ChipSpec> top;
        const juce::var* mainNode = nullptr;
        if (auto* is = sb.catalog["instruments"].getArray())
            for (auto& m : *is)
            {
                const auto id = m["id"].toString();
                if (sb.instSel.size() > 0 && sb.instSel[0] == id)
                    mainNode = &m;
                top.add ({ m["label"].toString(), sb.instSel.size() > 0 && sb.instSel[0] == id, [this, id] { pickInst (0, id); },
                           m["path"].toString() + " - " + m["count"].toString() + " samples" });
            }
        if (auto* rs = sb.catalog["roles"].getArray())
            for (auto& r : *rs)
            {
                const auto id = r["id"].toString(), label = r["label"].toString();
                top.add ({ label, sb.role == id, [this, id] {
                               sb.role = sb.role == id ? juce::String() : id;
                               sb.changed();
                           }, "All " + label.toLowerCase() + ", any instrument" });
            }
        flow (top, y, w, 3, 0);
        if (mainNode == nullptr)
            return;
        y += 4;
        juce::Array<ChipSpec> subs;
        const juce::var* subNode = nullptr;
        if (auto* ss = (*mainNode)["subs"].getArray())
            for (auto& s : *ss)
            {
                const auto key = s["kind"].toString() + ":" + s["value"].toString();
                const bool on = sb.instSel.size() > 1 && sb.instSel[1] == key;
                if (on)
                    subNode = &s;
                subs.add ({ s["label"].toString(), on, [this, key] { pickInst (1, key); }, s["label"].toString() });
            }
        sublevel ((*mainNode)["path"].toString(), subs, y, w, 1);
        if (subNode == nullptr)
            return;
        if (auto* kids = (*subNode)["children"].getArray(); kids != nullptr && ! kids->isEmpty())
        {
            juce::Array<ChipSpec> k;
            for (auto& c : *kids)
            {
                const auto id = c["id"].toString();
                k.add ({ c["label"].toString(), sb.instSel.size() > 2 && sb.instSel[2] == id, [this, id] { pickInst (2, id); },
                         c["path"].toString() + " - " + c["count"].toString() + " samples" });
            }
            sublevel ((*subNode)["label"].toString(), k, y, w, 2);
        }
    }

    void pickInst (int depth, const juce::String& key)
    {
        auto& sel = sb.instSel;
        if (sel.size() > depth && sel[depth] == key)
            sel.removeRange (depth, sel.size() - depth);   // the same again: off, the parent stays
        else
        {
            sel.removeRange (depth, sel.size() - depth);
            sel.add (key);
        }
        sb.changed();
    }

    void genres (int& y, int w)
    {
        auto* gs = sb.catalog["genres"].getArray();
        if (gs == nullptr || gs->isEmpty())
        {
            auto* t = new Title (sb.catalog.isVoid() ? "Loading..." : "No genres");
            items.add (t);
            addAndMakeVisible (t);
            t->setBounds (0, y, w, 22);
            y += 24;
            return;
        }
        juce::Array<ChipSpec> top;
        const juce::var* groupNode = nullptr;
        for (auto& g : *gs)
        {
            const auto id = g["id"].toString();
            const bool on = sb.genreSel.size() > 0 && sb.genreSel[0] == id;
            if (on)
                groupNode = &g;
            top.add ({ g["short"].toString(), on, [this, id] { pickGenre (0, id); },
                       g["label"].toString() + " - " + g["count"].toString() + " samples" });
        }
        flow (top, y, w, 3, 0);
        if (groupNode == nullptr)
            return;
        y += 4;
        juce::Array<ChipSpec> sub;
        if (auto* items2 = (*groupNode)["genres"].getArray())
            for (auto& g : *items2)
            {
                const auto id = g["id"].toString();
                sub.add ({ g["short"].toString(), sb.genreSel.size() > 1 && sb.genreSel[1] == id, [this, id] { pickGenre (1, id); },
                           g["label"].toString() + " - " + g["count"].toString() + " samples" });
            }
        sublevel ((*groupNode)["label"].toString(), sub, y, w, 1, 2);
    }

    void pickGenre (int depth, const juce::String& key)
    {
        auto& sel = sb.genreSel;
        if (sel.size() > depth && sel[depth] == key)
            sel.removeRange (depth, sel.size() - depth);
        else
        {
            sel.removeRange (depth, sel.size() - depth);
            sel.add (key);
        }
        sb.changed();
    }

    juce::String typeSummary() const
    {
        if (auto* ts = sb.catalog["types"].getArray())
            for (auto& t : *ts)
                if (t["name"].toString() == sb.type)
                    return t["label"].toString();
        return {};
    }

    juce::String instSummary() const
    {
        juce::StringArray parts;
        if (auto* is = sb.catalog["instruments"].getArray())
            for (auto& m : *is)
                if (sb.instSel.size() > 0 && m["id"].toString() == sb.instSel[0])
                {
                    juce::String label = m["path"].toString();
                    if (auto* ss = m["subs"].getArray(); ss != nullptr && sb.instSel.size() > 1)
                        for (auto& s : *ss)
                            if (s["kind"].toString() + ":" + s["value"].toString() == sb.instSel[1])
                            {
                                label = s["label"].toString();
                                if (auto* kids = s["children"].getArray(); kids != nullptr && sb.instSel.size() > 2)
                                    for (auto& c : *kids)
                                        if (c["id"].toString() == sb.instSel[2])
                                            label = c["label"].toString();
                            }
                    parts.add (label);
                }
        if (sb.role.isNotEmpty())
            parts.add (sb.role.substring (0, 1).toUpperCase() + sb.role.substring (1));
        return parts.joinIntoString (juce::String::fromUTF8 (" \xc2\xb7 "));
    }

    juce::String genreSummary() const { return sb.genreSel.isEmpty() ? juce::String() : sb.genreLabel (sb.genreSel[sb.genreSel.size() - 1]); }

    juce::String soundSummary() const
    {
        juce::StringArray s;
        for (auto& [g, n] : sb.sound)
            s.add (n.substring (0, 1).toUpperCase() + n.substring (1));
        return s.joinIntoString (", ");
    }

    void pick (const juce::String& v)
    {
        sb.view = v;
        if (sb.onViewSelected)
            sb.onViewSelected (v);
        sb.triggerAsyncUpdate();
    }

    void paint (juce::Graphics& g) override
    {
        // "Project key" label next to the combo box
        if (! keyBox.isVisible())
            return;
        g.setColour (theme::fg);
        g.setFont (theme::font (theme::small));
        g.drawText ("Project key", 26, keyBox.getY(), keyBox.getX() - 26, keyBox.getHeight(), juce::Justification::centredLeft);
    }

    Sidebar& sb;
    juce::OwnedArray<juce::Component> items;
    juce::ToggleButton sync { "Sync to host tempo" }, onlyTempo { "Only host tempo" }, onlyKey { "Only matching keys" },
        dragMatched { "Drag matched audio" };
    juce::ComboBox keyBox;
    RangeSlider tempo { 60, 200 };
    juce::TextButton clear;
};

// ---------------------------------------------------------------- Sidebar

Sidebar::Sidebar (FetchProcessor& p) : proc (p)
{
    loadState();
    content = std::make_unique<Content> (*this);
    viewport.setViewedComponent (content.get(), false);
    viewport.setScrollBarsShown (true, false);
    viewport.setScrollBarThickness (10);
    viewport.setWantsKeyboardFocus (false);   // the arrow keys belong to the results list
    addAndMakeVisible (viewport);
    gear = std::make_unique<GearButton>();
    gear->onClick = [this] { if (onSettings) onSettings (*gear); };
    addAndMakeVisible (*gear);
}

Sidebar::~Sidebar() = default;

void Sidebar::paint (juce::Graphics& g)
{
    g.fillAll (theme::sidebar);
    // brand, as in the Fetch window: the 52 px logo image (transparent background) 11 px from the top and left;
    // the name's capitals 22 px, text rows 28–49 (the design reference)
    const auto img = juce::Rectangle<float> ((float) brandInset, (float) brandInset, (float) brandImage, (float) brandImage);
    g.drawImage (theme::logo(), img, juce::RectanglePlacement::stretchToFit);
    // black in the light scheme, off-white (matched to the icon's white) in the dark one
    g.setColour (theme::dark ? juce::Colours::white.withAlpha (0.92f) : juce::Colours::black);
    const auto nameFont = theme::font (36.0f, true);   // capitals 22 px (the window's 30 px weight 800)
    g.setFont (nameFont);
    const int nx = juce::roundToInt (img.getRight()) + 5;
    const int baseline = juce::roundToInt (img.getY() + img.getHeight() * 328.0f / 512.0f) + 5;   // text rows 28–49, as in the window
    g.drawSingleLineText ("Fetch", nx, baseline);
    // the version, small and dim next to the name (the window's .sh-version)
    g.setColour (theme::fg.withMultipliedAlpha (0.55f));
    g.setFont (theme::font (11.0f));
    g.drawSingleLineText (FETCH_VERSION_STRING, nx + juce::GlyphArrangement::getStringWidthInt (nameFont, "Fetch") + 7, baseline - 2);
    g.setColour (theme::sidebarEdge);
    g.fillRect (getWidth() - 1, 0, 1, getHeight());
}

void Sidebar::paintOverChildren (juce::Graphics& g)
{
    // a short fade to half black at the bottom edge
    const float h = 40.0f, bottom = (float) getHeight();
    const auto black = juce::Colours::black;
    g.setGradientFill (juce::ColourGradient (black.withAlpha (0.0f), 0.0f, bottom - h,
                                             black.withAlpha (0.5f), 0.0f, bottom, false));
    g.fillRect (0.0f, bottom - h, (float) getWidth() - 1.0f, h);
}

void Sidebar::resized()
{
    viewport.setBounds (getLocalBounds().withTrimmedTop (brandHeight).withTrimmedRight (1));
    gear->setBounds (getWidth() - 34, 24, 28, 28);   // as in the window: centre 20 px from the right, y 38 (the name's middle)
    rebuild();
}

void Sidebar::rebuild()
{
    const int w = viewport.getWidth() - 2;
    if (w <= 0)
        return;
    const auto scroll = viewport.getViewPosition();
    const int h = content->build (w);
    content->setSize (w, h);
    viewport.setViewPosition (scroll);
    content->repaint();
}

void Sidebar::setCatalog (const juce::var& c)
{
    catalog = c;
    genreLabels = new juce::DynamicObject();
    if (auto* gs = c["genres"].getArray())
        for (auto& g : *gs)
        {
            genreLabels->setProperty (g["id"].toString(), g["label"].toString());
            if (auto* items = g["genres"].getArray())
                for (auto& x : *items)
                    genreLabels->setProperty (x["id"].toString(), x["label"].toString());
        }
    rebuild();
}

void Sidebar::treeRows (const juce::String& parent, int depth, std::function<void (const juce::var&, int)> add)
{
    const auto it = treeCache.find (parent);
    if (it == treeCache.end())
    {
        if (! treePending.contains (parent) && onNeedFolders)
        {
            treePending.add (parent);
            onNeedFolders (parent);
        }
        return;
    }
    if (auto* arr = it->second.getArray())
        for (auto& d : *arr)
        {
            add (d, depth);
            if (treeExpanded.contains (d["path"].toString()))
                treeRows (d["path"].toString(), depth + 1, add);
        }
}

void Sidebar::setFolders (const juce::String& parent, const juce::var& folders)
{
    treePending.removeString (parent);
    treeCache[parent] = folders.isArray() ? folders : juce::var (juce::Array<juce::var>());
    triggerAsyncUpdate();
}

void Sidebar::setHostStatus (const juce::String& s)
{
    if (s == hostStatus)
        return;
    hostStatus = s;
    for (auto* c : content->items)
        if (auto* h = dynamic_cast<SectionHead*> (c); h != nullptr && h->getButtonText() == "Host sync")
        {
            h->summary = s;
            h->repaint();
        }
}

juce::String Sidebar::filterQuery() const
{
    juce::StringArray parts;
    if (type.isNotEmpty())
        parts.add ("+" + type);
    for (auto& [g, n] : sound)
        parts.add ("+" + n);
    if (instSel.size() > 0)
    {
        juce::String inst = instSel[0], tag, typ;
        if (instSel.size() > 1)
        {
            const auto kind = instSel[1].upToFirstOccurrenceOf (":", false, false);
            const auto val = instSel[1].fromFirstOccurrenceOf (":", false, false);
            if (kind == "inst" || kind == "link")
                inst = val;
            else if (kind == "tag")
                tag = val;
            else if (kind == "type")
                typ = val;
        }
        if (instSel.size() > 2)
            inst = instSel[2];
        parts.add ("inst:" + inst);
        if (tag.isNotEmpty())
            parts.add ("tag:" + tag);
        if (typ.isNotEmpty() && type.isEmpty())
            parts.add ("+" + typ);
    }
    if (role.isNotEmpty())
        parts.add ("tag:" + role);
    if (genreSel.size() > 0)
        parts.add ("genre:" + genreSel[genreSel.size() - 1]);
    if (bpmLo > 60 || bpmHi < 200)
        parts.add ("bpm:" + juce::String (bpmLo) + "-" + juce::String (bpmHi));
    return parts.joinIntoString (" ");
}

void Sidebar::clearFilters (bool notify)
{
    type.clear();
    sound.clear();
    instSel.clear();
    role.clear();
    genreSel.clear();
    bpmLo = 60;
    bpmHi = 200;
    if (notify)
        changed();
    else
    {
        saveState();
        triggerAsyncUpdate();
    }
}

void Sidebar::changed()
{
    saveState();
    triggerAsyncUpdate();
    if (onFiltersChanged)
        onFiltersChanged();
}

void Sidebar::saveState()
{
    auto* o = new juce::DynamicObject();
    o->setProperty ("type", type);
    auto* s = new juce::DynamicObject();
    for (auto& [g, n] : sound)
        s->setProperty (g, n);
    o->setProperty ("sound", juce::var (s));
    o->setProperty ("inst", instSel.joinIntoString ("|"));
    o->setProperty ("role", role);
    o->setProperty ("genre", genreSel.joinIntoString ("|"));
    o->setProperty ("bpmLo", bpmLo);
    o->setProperty ("bpmHi", bpmHi);
    o->setProperty ("collapsed", collapsed.joinIntoString ("|"));
    o->setProperty ("recentOpen", recentOpen);
    o->setProperty ("treeOpen", treeOpen);
    o->setProperty ("treeExpanded", treeExpanded.joinIntoString ("\n"));
    proc.settings.setProperty ("filters", juce::JSON::toString (juce::var (o), true), nullptr);
}

void Sidebar::loadState()
{
    const auto v = juce::JSON::parse (proc.settings.getProperty ("filters", "").toString());
    if (! v.isObject())
        return;
    type = v["type"].toString();
    if (auto* s = v["sound"].getDynamicObject())
        for (auto& p : s->getProperties())
            sound[p.name.toString()] = p.value.toString();
    instSel = juce::StringArray::fromTokens (v["inst"].toString(), "|", "");
    instSel.removeEmptyStrings();
    role = v["role"].toString();
    genreSel = juce::StringArray::fromTokens (v["genre"].toString(), "|", "");
    genreSel.removeEmptyStrings();
    bpmLo = v.hasProperty ("bpmLo") ? (int) v["bpmLo"] : 60;
    bpmHi = v.hasProperty ("bpmHi") ? (int) v["bpmHi"] : 200;
    if (v.hasProperty ("collapsed"))
    {
        collapsed = juce::StringArray::fromTokens (v["collapsed"].toString(), "|", "");
        collapsed.removeEmptyStrings();
    }
    recentOpen = (bool) v["recentOpen"];
    treeOpen = (bool) v["treeOpen"];
    treeExpanded = juce::StringArray::fromLines (v["treeExpanded"].toString());
    treeExpanded.removeEmptyStrings();
}
