#include "PluginEditor.h"
#include "TagEditor.h"
#include "Prefs.h"

namespace
{
enum Columns { colHeart = 1, colName, colInst, colGenre, colKey, colBpm, colLen, colType, colPack };
constexpr int pageSize = 100;
// measured from the Fetch window: header bar 47, heading 50, list header 30, rows 40, footer 47
constexpr int headerHeight = 47, headingHeight = 50, footerHeight = 47, searchPad = 6;
constexpr int contentInset = 14;   // search field, title and heart icons start this far from the sidebar
constexpr int searchIndent = contentInset - searchPad;
int sidebarWidthFor (int w) { return juce::jlimit (250, 300, w / 4); }

const char* sortField (int col)
{
    switch (col)
    {
        case colName: return "name";
        case colInst: return "inst";
        case colGenre: return "genre";
        case colKey: return "key";
        case colBpm: return "bpm";
        case colLen: return "dur";
        case colType: return "type";
        case colPack: return "pack";
        default: return "";
    }
}

juce::String fmtBpm (double bpm)
{
    return std::abs (bpm - std::round (bpm)) < 0.05 ? juce::String ((int) std::round (bpm)) : juce::String (bpm, 1);
}

juce::String fmtTime (double s)
{
    if (s < 0)
        return "0:00";
    const int t = (int) s;
    return juce::String (t / 60) + ":" + juce::String (t % 60).paddedLeft ('0', 2);
}

juce::String fmtLen (double s)
{
    if (s < 10)
        return juce::String (s, 1) + " s";
    return fmtTime (s);
}

juce::String withoutRoot (const juce::String& pack)   // "Samples/02. Kits/…" -> "02. Kits/…" (as the window)
{
    return pack.containsChar ('/') ? pack.fromFirstOccurrenceOf ("/", false, false) : pack;
}
} // namespace

// ---------------------------------------------------------------- small components

void IconButton::paintButton (juce::Graphics& g, bool over, bool down)
{
    auto r = getLocalBounds().toFloat().reduced (2.0f);
    if (active || over || down)
    {
        g.setColour (active ? theme::buttonHover : down ? theme::buttonPressed : theme::buttonBg);
        g.fillRoundedRectangle (r, 6.0f);
    }
    theme::drawIcon (g, icon, r.withSizeKeepingCentre (16.0f, 16.0f), isEnabled() ? theme::fg : theme::faint, ! stroked, 1.6f);
}

void SeekBar::paint (juce::Graphics& g)
{
    // GtkScale: 6 px trough, accent fill, round knob with a soft shadow
    const float cy = getHeight() * 0.5f, x0 = 10.0f, w = (float) getWidth() - 20.0f;
    g.setColour (theme::trough);
    g.fillRoundedRectangle (x0, cy - 3.0f, w, 6.0f, 3.0f);
    const float x = x0 + (float) (enabled ? value : 0.0) * w;
    if (enabled)
    {
        g.setColour (theme::accentBg);
        g.fillRoundedRectangle (x0, cy - 3.0f, x - x0, 6.0f, 3.0f);
    }
    g.setColour (juce::Colour (0x33000000));
    g.fillEllipse (x - 10.0f, cy - 9.0f, 20.0f, 20.0f);
    g.setColour (theme::knob);
    g.fillEllipse (x - 10.0f, cy - 10.0f, 20.0f, 20.0f);
}

void SeekBar::mouseDrag (const juce::MouseEvent& e)
{
    if (! enabled)
        return;
    value = juce::jlimit (0.0, 1.0, (e.x - 8.0) / (getWidth() - 16.0));
    repaint();
    if (onSeek)
        onSeek (value);
}

// ---------------------------------------------------------------- editor

FetchEditor::FetchEditor (FetchProcessor& p)
    : AudioProcessorEditor (p), proc (p), sidebar (p)
{
    {
        bool dark = false;
        juce::Colour accent;
        theme::detectScheme (dark, accent);   // ~10 ms: avoids a flash of the wrong scheme
        prefs::loadAppearance (theme::uiScale, theme::fontScale);
        theme::setScheme (dark, accent);
        lnf.refresh();
    }
    setLookAndFeel (&lnf);
    juce::LookAndFeel::setDefaultLookAndFeel (&lnf);   // popup menus and tooltips
    juce::Desktop::getInstance().addFocusChangeListener (this);
    auto& st = proc.settings;
    setWantsKeyboardFocus (true);

    addAndMakeVisible (sidebar);
    sidebar.onFiltersChanged = [this] { reload(); };
    sidebar.onViewSelected = [this] (const juce::String& v) { openView (v); };
    sidebar.onSettings = [this] (juce::Component& anchor) { openSettings (anchor); };
    sidebar.onNeedFolders = [this] (const juce::String& parent) {
        auto* client = &proc.client;
        pool->addJob ([this, client, parent, flag = alive] {
            juce::String err;
            auto r = client->folders (parent, err);
            juce::MessageManager::callAsync ([this, parent, r, err, flag] {
                if (*flag)
                    sidebar.setFolders (parent, err.isEmpty() ? r["folders"] : juce::var (juce::Array<juce::var>()));
            });
        });
    };
    sidebar.onRecent = [this] (const juce::String& q) {
        search.setText (q, false);
        searchDueAt = 0;
        similarTitle.clear();
        committedQuery = q;
        reload();
        table.grabKeyboardFocus();
    };
    sidebar.onSyncChanged = [this] { table.repaint(); };

    // search entry (rounded, icon at the left, centred in the header bar)
    search.setFont (theme::font());
    search.setIndents (34, 0);
    juce::Desktop::getInstance().addGlobalMouseListener (this);   // clicks anywhere: see mouseDown
    search.setJustification (juce::Justification::centredLeft);
    search.setText (st.getProperty ("query", "").toString(), false);
    search.onTextChange = [this] { searchDueAt = juce::Time::getMillisecondCounter() + 200; };
    search.onReturnKey = [this] {
        searchWanted = false;
        searchDueAt = 0;
        similarTitle.clear();
        reload();
        commitSearch();
        table.grabKeyboardFocus();
    };
    // As in the Fetch window: Down (or Page Down) in the search field commits the search and moves into
    // the list. TextEditor would otherwise eat the key (caret to the end) and the list never sees it.
    searchKeys.fn = [this] (const juce::KeyPress& k) {
        if (k.getKeyCode() != juce::KeyPress::downKey && k.getKeyCode() != juce::KeyPress::pageDownKey)
            return false;
        searchWanted = false;
        if (searchDueAt != 0)   // typed text not searched yet: search now, enter the list when it arrives
        {
            searchDueAt = 0;
            saveSettings();
            reload();
        }
        commitSearch();
        table.grabKeyboardFocus();
        if (loading)
            enterListOnLoad = true;
        else
            enterList();
        return true;
    };
    search.addKeyListener (&searchKeys);
    search.onEscapeKey = [this] {
        searchWanted = false;
        table.grabKeyboardFocus();
    };
    addAndMakeVisible (search);

    title.setFont (theme::font (17.0f, true));
    title.setMinimumHorizontalScale (0.7f);
    addAndMakeVisible (title);
    subtitle.setFont (theme::font());
    addAndMakeVisible (subtitle);

    shuffleButton.onClick = [this] {
        shuffled = true;
        seed = juce::Random::getSystemRandom().nextInt (1 << 30);
        sortColumn = 0;
        table.getHeader().setSortColumnId (0, true);
        reload();
    };
    unshuffleButton.onClick = [this] {
        shuffled = false;
        reload();
    };
    addChildComponent (shuffleButton);
    addChildComponent (unshuffleButton);

    // results list
    auto& h = table.getHeader();
    const int sortable = juce::TableHeaderComponent::visible | juce::TableHeaderComponent::resizable | juce::TableHeaderComponent::sortable;
    // the Fetch window's columns and widths
    h.addColumn ({}, colHeart, 40, 40, 40, juce::TableHeaderComponent::visible);
    h.addColumn ("Name", colName, 340, 80, -1, sortable);
    h.addColumn ("Instrument", colInst, 130, 40, -1, sortable);
    h.addColumn ("Genre", colGenre, 120, 40, -1, sortable);
    h.addColumn ("Key", colKey, 70, 36, -1, sortable);
    h.addColumn ("BPM", colBpm, 70, 36, -1, sortable);
    h.addColumn ("Length", colLen, 70, 36, -1, sortable);
    h.addColumn ("Type", colType, 85, 36, -1, sortable);
    h.addColumn ("Pack", colPack, 300, 60, -1, sortable);
    h.setStretchToFitActive (true);
    h.setPopupMenuActive (false);
    if (auto layout = st.getProperty ("columns", "").toString(); layout.isNotEmpty() && (int) st.getProperty ("layoutVersion", 0) == 2)
        h.restoreFromString (layout);
    sortColumn = st.getProperty ("sortColumn", 0);
    sortForwards = st.getProperty ("sortForwards", true);
    h.setSortColumnId (sortColumn, sortForwards);
    table.setHeaderHeight (30);
    table.setRowHeight (40);
    table.setMultipleSelectionEnabled (true);
    table.setOutlineThickness (0);
    addAndMakeVisible (table);

    // player bar
    playButton.onClick = [this] {
        if (proc.isPreviewing())
            proc.togglePause();
        else
            playRow (table.getSelectedRow());
    };
    addAndMakeVisible (playButton);
    now.setFont (theme::font());
    addAndMakeVisible (now);
    seek.onSeek = [this] (double v) { proc.seek (v); };
    addAndMakeVisible (seek);
    timeLabel.setFont (theme::font());
    timeLabel.setJustificationType (juce::Justification::centredRight);
    addAndMakeVisible (timeLabel);
    autoplay = st.getProperty ("autoPlay", true);
    autoplayButton.active = autoplay;
    autoplayButton.onClick = [this] { openPlayerMenu(); };
    addAndMakeVisible (autoplayButton);
    volume.setTooltip ("Preview volume");
    volumeAttachment = std::make_unique<juce::AudioProcessorValueTreeState::SliderAttachment> (proc.params, "volume", volume);
    volume.setWantsKeyboardFocus (false);
    addAndMakeVisible (volume);

    view = st.getProperty ("view", "ALL").toString();
    if (view != "ALL" && ! localFile (view.startsWith ("d:") ? view.substring (2) : view).isDirectory())
        view = "ALL";
    sidebar.setView (view);

    setResizable (true, true);
    setResizeLimits (820, 480, 3000, 2000);
    setSize ((int) st.getProperty ("width", 1150), (int) st.getProperty ("height", 700));

    applyTheme();
    setTitle ("Connecting...", {});
    loadCatalog();
    reload();
    startTimerHz (20);
}

FetchEditor::~FetchEditor()
{
    *alive = false;
    juce::Desktop::getInstance().removeFocusChangeListener (this);
    juce::Desktop::getInstance().removeGlobalMouseListener (this);
    search.removeKeyListener (&searchKeys);
    tagEditor.reset();
    saveSettings();
    pool->removeAllJobs (true, 10000);
    table.setModel (nullptr);
    juce::LookAndFeel::setDefaultLookAndFeel (nullptr);
    setLookAndFeel (nullptr);
}

void FetchEditor::saveSettings()
{
    auto& st = proc.settings;
    st.setProperty ("query", search.getText(), nullptr);
    st.setProperty ("view", view, nullptr);
    st.setProperty ("autoPlay", autoplay, nullptr);
    st.setProperty ("width", getWidth(), nullptr);
    st.setProperty ("height", getHeight(), nullptr);
    st.setProperty ("columns", table.getHeader().toString(), nullptr);
    st.setProperty ("layoutVersion", 2, nullptr);
    st.setProperty ("sortColumn", sortColumn, nullptr);
    st.setProperty ("sortForwards", sortForwards, nullptr);
}

// ---------------------------------------------------------------- layout

// Settings (the gear in the sidebar header), as in the Fetch window: appearance for now.
namespace
{
struct SettingsPanel : juce::Component, private juce::Timer
{
    SettingsPanel (FetchClient& c, std::function<void()> changed) : client (c), onChanged (std::move (changed))
    {
        auto setup = [this] (juce::Slider& s, float v) {
            s.setSliderStyle (juce::Slider::LinearHorizontal);
            s.setTextBoxStyle (juce::Slider::TextBoxRight, true, 52, 22);
            s.setRange (75.0, 150.0, 5.0);
            s.setTextValueSuffix (" %");
            s.setValue (std::round (v * 100.0f), juce::dontSendNotification);
            s.setDoubleClickReturnValue (true, 100.0);
            s.onValueChange = [this] { save(); };
            addAndMakeVisible (s);
        };
        setup (ui, theme::uiScale);
        setup (font, theme::fontScale);
        reset.setButtonText ("Reset");
        reset.getProperties().set ("flat", true);
        reset.onClick = [this] { ui.setValue (100.0); font.setValue (100.0); };
        addAndMakeVisible (reset);
        add.setButtonText (juce::String::fromUTF8 ("Add folder\xe2\x80\xa6"));
        add.onClick = [this] { pickFolder(); };
        addAndMakeVisible (add);
        setSize (460, 330);
        loadRoots();
        poll();
        startTimer (2000);
    }
    ~SettingsPanel() override { *alive = false; }

    // runs fn on a background thread, then done(result) on the message thread if the panel still exists
    void async (std::function<juce::var (juce::String&)> fn, std::function<void (const juce::var&)> done)
    {
        juce::Thread::launch ([fn, done, flag = alive] {
            juce::String err;
            auto r = fn (err);
            juce::MessageManager::callAsync ([r, err, done, flag] {
                if (*flag && err.isEmpty())
                    done (r);
            });
        });
    }
    void loadRoots()
    {
        async ([this] (juce::String& e) { return client.folders ({}, e); }, [this] (const juce::var& r) {
            roots.clear();
            removeButtons.clear();
            if (auto* a = r["folders"].getArray())
                for (auto& f : *a)
                {
                    roots.add (f["name"].toString() + juce::String::fromUTF8 (" \xc2\xb7 ") + juce::String ((int) f["count"])
                               + " samples\n" + f["path"].toString());
                    auto* b = removeButtons.add (new IconButton (theme::trash(), "Remove from the library", true));
                    b->onClick = [this, name = f["name"].toString(), path = f["path"].toString(), n = (int) f["count"]] {
                        confirmRemove (name, path, n);
                    };
                    addAndMakeVisible (b);
                }
            setSize (getWidth(), 250 + 40 * roots.size());   // the call-out box follows (childBoundsChanged)
            resized();
            repaint();
        });
    }
    void poll()
    {
        async ([this] (juce::String& e) { return client.indexStatus (e); }, [this] (const juce::var& st) { showStatus (st); });
    }
    void timerCallback() override { poll(); }
    void pickFolder()
    {
        chooser = std::make_unique<juce::FileChooser> ("Add a folder to the library");
        chooser->launchAsync (juce::FileBrowserComponent::openMode | juce::FileBrowserComponent::canSelectDirectories,
                              [this] (const juce::FileChooser& fc) {
                                  const auto f = fc.getResult();
                                  if (! f.isDirectory())
                                      return;
                                  const auto path = f.getFullPathName();
                                  async ([this, path] (juce::String& e) { return client.addFolder (path, e); },
                                         [this] (const juce::var& st) { showStatus (st); });
                              });
    }
    // Remove folder: only out of Fetch's library (the service's removefolder); the files on disk stay
    void confirmRemove (const juce::String& name, const juce::String& path, int count)
    {
        auto opts = juce::MessageBoxOptions()
                        .withIconType (juce::MessageBoxIconType::WarningIcon)
                        .withTitle (juce::String::fromUTF8 ("Remove \xe2\x80\x9c") + name + juce::String::fromUTF8 ("\xe2\x80\x9d from the library?"))
                        .withMessage ("Its " + juce::String (count) + " samples will no longer be searchable in Fetch. The files on disk "
                                      "are not deleted, and your likes and tag corrections are kept. Adding the folder again "
                                      "analyses it again.")
                        .withButton ("Remove")
                        .withButton ("Cancel")
                        .withAssociatedComponent (this);
        juce::AlertWindow::showAsync (opts, [this, path, flag = alive] (int button) {
            if (! *flag || button != 1)
                return;
            juce::Thread::launch ([this, path, flag] {
                juce::String err;
                auto r = client.removeFolder (path, err);
                juce::MessageManager::callAsync ([this, r, err, flag] {
                    if (! *flag)
                        return;
                    holdUntil = juce::Time::getMillisecondCounter() + 30000;
                    if (err.isNotEmpty())
                    {
                        statusTitle = "Could not remove the folder";
                        statusLine = err;
                    }
                    else
                    {
                        statusTitle = "Removed " + r["folder"].toString().fromLastOccurrenceOf ("/", false, false) + " from the library";
                        statusLine = juce::String ((int) r["removed"]) + " samples left the index. The files on disk were not touched.";
                        loadRoots();
                        if (onLibraryChanged)
                            onLibraryChanged();
                    }
                    repaint();
                });
            });
        });
    }
    void showStatus (const juce::var& st)
    {
        if (st["state"].toString() != "running" && juce::Time::getMillisecondCounter() < holdUntil)
            return;                                      // "Removed …" stays readable for a while
        const auto state = st["state"].toString();
        const auto err = st["error"].toString();
        statusTitle.clear();
        statusLine.clear();
        if (err.isNotEmpty() && state != "running")
        {
            statusTitle = "Analysis stopped";
            statusLine = err;
        }
        else if (state == "running")
        {
            const auto name = st["folder"].toString().fromLastOccurrenceOf ("/", false, false);
            statusTitle = "Analysing " + name;
            if ((int) st["steps"] > 0)
                statusTitle << " " << juce::String::fromUTF8 ("\xc2\xb7") << " step " << (int) st["step"] << "/" << (int) st["steps"];
            statusLine = (st["title"].toString() + " " + st["line"].toString()).trim();
            wasRunning = true;
        }
        else if (state == "done" && wasRunning)
        {
            statusTitle = "Analysis finished";
            statusLine = "The new samples appear within a minute.";
            wasRunning = false;
            loadRoots();
        }
        repaint();
    }
    void save()
    {
        prefs::saveAppearance ((float) ui.getValue() / 100.0f, (float) font.getValue() / 100.0f);
        if (onChanged)
            onChanged();
    }
    void paint (juce::Graphics& g) override
    {
        auto head = [&] (const juce::String& t, int y) {
            g.setColour (theme::fg);
            g.setFont (theme::font (theme::body, true));
            g.drawText (t, 16, y, 240, 24, juce::Justification::centredLeft);
        };
        auto text = [&] (const juce::String& t, int y, bool dim, float size = theme::body) {
            g.setColour (dim ? theme::dim : theme::fg);
            g.setFont (theme::font (size));
            g.drawText (t, 16, y, getWidth() - 64, 18, juce::Justification::centredLeft, true);
        };
        head ("Appearance", 12);
        text ("UI scaling", 50, false);
        text ("Text, icons and spacing", 68, true, 12.0f);
        text ("Font size", 100, false);
        text ("Text only", 118, true, 12.0f);
        head ("Library folders", 156);
        int y = 190;
        for (auto& r : roots)
        {
            text (r.upToFirstOccurrenceOf ("\n", false, false), y, false);
            text (r.fromFirstOccurrenceOf ("\n", false, false), y + 17, true, 12.0f);
            y += 40;
        }
        if (statusTitle.isNotEmpty())
        {
            text (statusTitle, y + 4, false);
            text (statusLine, y + 21, true, 12.0f);
        }
    }
    void resized() override
    {
        reset.setBounds (getWidth() - 86, 12, 70, 26);
        ui.setBounds (170, 52, getWidth() - 186, 28);
        font.setBounds (170, 102, getWidth() - 186, 28);
        add.setBounds (getWidth() - 126, 154, 110, 28);
        for (int i = 0; i < removeButtons.size(); ++i)
            removeButtons[i]->setBounds (getWidth() - 46, 190 + 40 * i + 4, 30, 30);
    }
    FetchClient& client;
    juce::Slider ui, font;
    juce::TextButton reset, add;
    std::unique_ptr<juce::FileChooser> chooser;
    juce::StringArray roots;
    juce::OwnedArray<IconButton> removeButtons;
    juce::uint32 holdUntil = 0;
    juce::String statusTitle, statusLine;
    std::function<void()> onLibraryChanged;    // a folder was removed: the sidebar's Folders tree reloads
    bool wasRunning = false;
    std::shared_ptr<bool> alive = std::make_shared<bool> (true);
    std::function<void()> onChanged;
};
} // namespace

void FetchEditor::openSettings (juce::Component& anchor)
{
    auto panel = std::make_unique<SettingsPanel> (proc.client, [sp = juce::Component::SafePointer<FetchEditor> (this)] {
        if (sp != nullptr)
            sp->checkAppearance();
    });
    panel->onLibraryChanged = [sp = juce::Component::SafePointer<FetchEditor> (this)] {
        if (sp != nullptr)
            sp->sidebar.foldersChanged();
    };
    juce::CallOutBox::launchAsynchronously (std::move (panel), anchor.getScreenBounds(), nullptr);
}

// Player options (was the Play on select toggle), as in the Fetch window's pop-over.
namespace
{
struct PlayerPanel : juce::Component, private juce::Timer
{
    struct Hooks
    {
        std::function<bool()> autoplay, loop;
        std::function<void (bool)> setAutoplay, setLoop;
        std::function<juce::String()> lengthText;   // "" = no loop to set (the row shows why)
        std::function<bool()> canSetLength;
        std::function<void (double)> changeLength;
    };
    explicit PlayerPanel (Hooks h) : hooks (std::move (h))
    {
        auto toggle = [this] (juce::ToggleButton& b, bool on, std::function<void (bool)> set) {
            b.setToggleState (on, juce::dontSendNotification);
            b.setWantsKeyboardFocus (false);
            b.onClick = [&b, set] { set (b.getToggleState()); };
            addAndMakeVisible (b);
        };
        toggle (autoplay, hooks.autoplay(), hooks.setAutoplay);
        toggle (loop, hooks.loop(), hooks.setLoop);
        for (auto* b : { &half, &twice, &automatic })
        {
            b->setWantsKeyboardFocus (false);
            addAndMakeVisible (b);
        }
        half.setTooltip ("Count the loop as half as long: plays half-time (174 BPM break in a 120 project at 87)");
        twice.setTooltip ("Count the loop as twice as long: plays double-time");
        automatic.setTooltip ("Fetch decides: beat for beat, or half / double when that is within ~25 % of the host tempo");
        automatic.getProperties().set ("flat", true);
        half.onClick = [this] { hooks.changeLength (0.5); update(); };
        twice.onClick = [this] { hooks.changeLength (2.0); update(); };
        automatic.onClick = [this] { hooks.changeLength (0.0); update(); };
        setSize (380, 208);
        update();
        startTimer (300);     // follows the playing / selected sample and host tempo
    }
    void timerCallback() override { update(); }
    void update()
    {
        const bool can = hooks.canSetLength();
        for (auto* b : { &half, &twice, &automatic })
            b->setEnabled (can);
        if (auto t = hooks.lengthText(); t != length)
        {
            length = t;
            repaint();
        }
    }
    void paint (juce::Graphics& g) override
    {
        auto text = [&] (const juce::String& t, int y, bool dim, float size = theme::body, bool bold = false) {
            g.setColour (dim ? theme::dim : theme::fg);
            g.setFont (theme::font (size, bold));
            g.drawText (t, 16, y, getWidth() - 32, 18, juce::Justification::centredLeft, true);
        };
        text ("Player", 12, false, theme::body, true);
        text ("Up/Down in the list plays the sample", 66, true, 12.0f);
        text ("Loops repeat until stopped", 110, true, 12.0f);
        text ("Loop length", 140, false, theme::small);
        text (length, 182, true, 12.0f);
    }
    void resized() override
    {
        autoplay.setBounds (12, 44, getWidth() - 24, 22);
        loop.setBounds (12, 88, getWidth() - 24, 22);
        int x = getWidth() - 16;
        for (auto* b : { &automatic, &twice, &half })
        {
            const int w = b == &automatic ? 52 : 40;
            x -= w;
            b->setBounds (x, 138, w, 26);
            x -= 6;
        }
    }
    Hooks hooks;
    juce::ToggleButton autoplay { "Play on select" }, loop { "Loop preview" };
    juce::TextButton half { juce::String::fromUTF8 ("\xc3\xb7" "2") }, twice { juce::String::fromUTF8 ("\xc3\x97" "2") },
        automatic { "Auto" };
    juce::String length;
};
} // namespace

const SampleInfo* FetchEditor::lengthTarget() const
{
    if (auto* s = proc.currentSample())
        return s;
    const int row = table.getSelectedRow();
    return juce::isPositiveAndBelow (row, results.size()) ? &results.getReference (row) : nullptr;
}

juce::String FetchEditor::lengthText() const
{
    const auto* s = lengthTarget();
    if (s == nullptr || ! s->isLoop() || s->bpm <= 0)
        return "For loops with a known tempo";
    if (! proc.syncOn())
        return "Sync to host tempo is off";
    const auto p = proc.planFor (*s);
    auto num = [] (double v) { return juce::String (v, std::abs (v - std::round (v)) < 0.05 ? 0 : 1); };
    return num (p.beats) + " beats " + juce::String::fromUTF8 ("\xc2\xb7 ") + num (p.sampleBpm)
           + juce::String::fromUTF8 (" \xe2\x86\x92 ") + num (p.hostBpm) + " BPM" + (s->lenMult > 0 ? "" : "  (auto)");
}

void FetchEditor::changeLength (double factor)
{
    const auto* t = lengthTarget();
    if (t == nullptr || ! t->isLoop() || t->bpm <= 0 || ! proc.syncOn())
        return;
    const auto s = *t;
    double mult = 0.0;
    if (factor > 0)
    {
        const double cur = proc.planFor (s).sampleBpm / s.bpm;   // what plays now (automatic or chosen)
        mult = juce::jlimit (0.25, 4.0, std::exp2 (std::round (std::log2 (cur * factor))));
        if (std::abs (mult - cur) < 1e-6)
            return;
    }
    for (auto& r : results)
        if (r.path == s.path)
            r.lenMult = mult;
    proc.setLength (s.path, mult);
    auto* tags = new juce::DynamicObject();
    tags->setProperty ("len", mult > 0 ? juce::var (mult) : juce::var());
    juce::var tv (tags);
    pool->addJob ([this, path = s.path, tv] {
        juce::String err;
        proc.client.setTags ({ path }, tv, err);      // remembered by the service (korjaukset.json "len")
        if (err.isNotEmpty())
            juce::MessageManager::callAsync ([this, err, flag = alive] {
                if (*flag)
                    setTitle (title.getText(), err);
            });
    });
}

void FetchEditor::openPlayerMenu()
{
    PlayerPanel::Hooks h;
    juce::Component::SafePointer<FetchEditor> sp (this);
    h.autoplay = [sp] { return sp != nullptr && sp->autoplay; };
    h.loop = [sp] { return sp != nullptr && sp->proc.loopPreviewOn(); };
    h.setAutoplay = [sp] (bool on) {
        if (sp == nullptr)
            return;
        sp->autoplay = on;
        sp->autoplayButton.active = on;
        sp->autoplayButton.repaint();
        sp->saveSettings();
    };
    h.setLoop = [sp] (bool on) {
        if (sp != nullptr)
            sp->proc.settings.setProperty ("loopPreview", on, nullptr);
    };
    h.lengthText = [sp] { return sp != nullptr ? sp->lengthText() : juce::String(); };
    h.canSetLength = [sp] {
        const auto* s = sp != nullptr ? sp->lengthTarget() : nullptr;
        return s != nullptr && s->isLoop() && s->bpm > 0 && sp->proc.syncOn();
    };
    h.changeLength = [sp] (double f) {
        if (sp != nullptr)
            sp->changeLength (f);
    };
    auto panel = std::make_unique<PlayerPanel> (std::move (h));
    juce::CallOutBox::launchAsynchronously (std::move (panel), autoplayButton.getScreenBounds(), nullptr);
}

juce::Rectangle<int> FetchEditor::virtualBounds() const
{
    return { juce::roundToInt ((float) getWidth() / theme::uiScale), juce::roundToInt ((float) getHeight() / theme::uiScale) };
}

void FetchEditor::checkAppearance()
{
    if (! prefs::loadAppearance (theme::uiScale, theme::fontScale))
        return;
    lnf.refresh();
    resized();
    sidebar.resized();      // rebuilds its rows with the new fonts
    table.getHeader().resized();
    repaint();
}

void FetchEditor::paint (juce::Graphics& g)
{
    g.addTransform (juce::AffineTransform::scale (theme::uiScale));
    // content: header bar, heading and footer on the window colour, the list on the view colour
    g.setColour (theme::window);
    g.fillRect (virtualBounds().withTrimmedLeft (sidebar.getRight()));
    g.setColour (theme::view);
    g.fillRect (table.getBounds());
    g.setColour (theme::border);
    g.fillRect (table.getX(), table.getBottom(), table.getWidth(), 1);
}

void FetchEditor::paintOverChildren (juce::Graphics& g)
{
    g.addTransform (juce::AffineTransform::scale (theme::uiScale));
    auto s = search.getBounds().toFloat();
    theme::drawIcon (g, theme::search(), { s.getX() + 12.0f, s.getCentreY() - 7.5f, 15.0f, 15.0f }, theme::fg, false, 1.8f);

    if (loading)   // spinner at the right end of the search field
    {
        const juce::Point<float> c (s.getRight() - 18.0f, s.getCentreY());
        juce::Path arc;
        arc.addCentredArc (c.x, c.y, 7.0f, 7.0f, spin, 0.0f, juce::MathConstants<float>::pi * 1.4f, true);
        g.setColour (theme::dim);
        g.strokePath (arc, juce::PathStrokeType (2.0f, juce::PathStrokeType::curved, juce::PathStrokeType::rounded));
    }

    if (results.isEmpty() && loading && proc.client.serviceLoading)   // the service is still loading the library
    {
        auto r = table.getBounds().toFloat().withTrimmedTop (30.0f);
        auto c = r.withSizeKeepingCentre (juce::jmin (r.getWidth(), 480.0f), 150.0f);
        g.setColour (theme::fg);
        g.setFont (theme::font (22.0f, true));
        g.drawText (juce::String::fromUTF8 ("Loading your library\xe2\x80\xa6"), c.removeFromTop (32.0f).toNearestInt(),
                    juce::Justification::centred);
        g.setColour (theme::dim);
        g.setFont (theme::font());
        g.drawFittedText ("The first start after an update or a big indexing run can take a few minutes.",
                          c.toNearestInt(), juce::Justification::centredTop, 3);
    }
    if (results.isEmpty() && ! loading)   // Adw.StatusPage
    {
        auto r = table.getBounds().toFloat().withTrimmedTop (30.0f);
        auto c = r.withSizeKeepingCentre (juce::jmin (r.getWidth(), 480.0f), 180.0f);
        theme::drawIcon (g, theme::note(), c.removeFromTop (72.0f).withSizeKeepingCentre (64.0f, 64.0f), theme::faint, false, 1.2f);
        c.removeFromTop (12.0f);
        g.setColour (theme::fg);
        g.setFont (theme::font (22.0f, true));
        g.drawText (errorText.isNotEmpty() ? "Fetch is not reachable" : "No samples", c.removeFromTop (32.0f).toNearestInt(),
                    juce::Justification::centred);
        g.setColour (theme::dim);
        g.setFont (theme::font());
        g.drawFittedText (errorText.isNotEmpty() ? errorText : "Search above, or pick a collection on the left.",
                          c.toNearestInt(), juce::Justification::centredTop, 3);
    }
}

void FetchEditor::resized()
{
    auto r = virtualBounds();
    sidebar.setBounds (r.removeFromLeft (sidebarWidthFor (r.getWidth())));

    // the search field fills the header bar with the same padding on every side
    search.setBounds (r.removeFromTop (headerHeight).reduced (searchPad).withTrimmedLeft (searchIndent).withTrimmedRight (searchIndent));

    auto fb = r.removeFromBottom (footerHeight).reduced (12, 6);
    playButton.setBounds (fb.removeFromLeft (34).withSizeKeepingCentre (34, 34));
    fb.removeFromLeft (8);
    volume.setBounds (fb.removeFromRight (110));
    fb.removeFromRight (6);
    autoplayButton.setBounds (fb.removeFromRight (34).withSizeKeepingCentre (34, 34));
    fb.removeFromRight (6);
    timeLabel.setBounds (fb.removeFromRight (100));
    now.setBounds (fb.removeFromLeft (juce::jmin (300, fb.getWidth() / 3)));
    seek.setBounds (fb);

    auto head = r.removeFromTop (headingHeight).reduced (14, 8);
    head.setLeft (r.getX() + contentInset - title.getBorderSize().getLeft());   // the Label adds its own inset
    shuffleButton.setBounds (head.removeFromRight (34).withSizeKeepingCentre (34, 34));
    unshuffleButton.setBounds (head.removeFromRight (34).withSizeKeepingCentre (34, 34));
    const int tw = juce::jmin (head.getWidth() - 140, juce::GlyphArrangement::getStringWidthInt (title.getFont(), title.getText()) + 12);
    title.setBounds (head.removeFromLeft (juce::jmax (100, tw)));
    subtitle.setBounds (head);

    table.setBounds (r.withTrimmedBottom (1));
    if (tagEditor != nullptr)
        tagEditor->setBounds (virtualBounds());
    const auto t = juce::AffineTransform::scale (theme::uiScale);
    for (auto* c : getChildren())
        c->setTransform (t);
}

void FetchEditor::setTitle (const juce::String& t, const juce::String& sub)
{
    title.setText (t, juce::dontSendNotification);
    subtitle.setText (sub, juce::dontSendNotification);
    resized();
}

// ---------------------------------------------------------------- loading

juce::String FetchEditor::allFilters() const
{
    juce::StringArray f;
    const auto side = sidebar.filterQuery();
    f.add (side);
    if ((bool) proc.settings.getProperty ("onlyTempo", false) && ! side.contains ("+oneshot"))
        f.add ("bpm:" + fmtBpm (proc.hostBpm.load()));
    if ((bool) proc.settings.getProperty ("onlyKey", false) && proc.projectKey().isNotEmpty())
        f.add ("key:" + proc.projectKey() + "~");
    f.removeEmptyStrings();
    return f.joinIntoString (" ");
}

juce::String FetchEditor::order() const
{
    if (sortColumn > 0 && juce::String (sortField (sortColumn)).isNotEmpty())
        return juce::String ("sort:") + sortField (sortColumn) + (sortForwards ? ":asc" : ":desc");
    return shuffled ? "shuffle" : "path";
}

void FetchEditor::openView (const juce::String& v)
{
    view = v;
    similarTitle.clear();
    search.setText ({}, false);
    searchDueAt = 0;
    saveSettings();
    reload();
}

void FetchEditor::reload (bool append)
{
    auto* client = &proc.client;
    const auto text = search.getText().trim();
    const auto flt = allFilters();
    const auto ord = order();
    const int s = seed;
    if (! append)
        similarTitle.clear();
    const int offset = append ? results.size() : 0;
    if (text.isNotEmpty())
        runQuery ([=] { return client->browse (text, flt, offset, pageSize, ord, s); }, append,
                  juce::String::fromUTF8 ("\xe2\x80\x9c") + text + juce::String::fromUTF8 ("\xe2\x80\x9d"), true);
    else if (view == "ALL")
        runQuery ([=] { return client->browse ({}, flt, offset, pageSize, ord, s); }, append, "All samples", true);
    else if (view.startsWith ("d:"))     // a library folder from the Folders tree, page by page
    {
        const auto under = view.substring (2);
        runQuery ([=] { return client->browse ({}, flt, offset, pageSize, ord, s, under); }, append,
                  under.fromLastOccurrenceOf ("/", false, false), true);
    }
    else
    {
        const auto folder = view;
        runQuery ([=] { return client->folder (folder, flt, ord); }, false, juce::File (folder).getFileName(), false);
    }
}

void FetchEditor::showSimilar (int row)
{
    if (! juce::isPositiveAndBelow (row, results.size()))
        return;
    const auto src = results[row];
    juce::StringArray conds { "timbre" };
    if ((bool) proc.settings.getProperty ("onlyKey", false))
        conds.add ("key");
    if ((bool) proc.settings.getProperty ("onlyTempo", false))
        conds.add ("bpm");
    auto* client = &proc.client;
    const auto path = src.path;
    sidebar.clearFilters (false);   // a Similar list shows all its matches, as in the Fetch window
    runQuery ([client, path, conds] { return client->similar (path, conds, 100); }, false,
              juce::String::fromUTF8 ("Similar \xe2\x80\x93 ") + juce::File (src.path).getFileNameWithoutExtension(), false);
    similarTitle = src.name;
}

void FetchEditor::runQuery (std::function<PageResult()> fn, bool append, const juce::String& what, bool isPaged)
{
    const int gen = ++generation;
    loading = true;
    repaint();
    if (! append)
        setTitle (what, {});
    pool->addJob ([this, fn, append, what, gen, isPaged, flag = alive] {
        auto res = std::make_shared<PageResult> (fn());
        juce::MessageManager::callAsync ([this, res, append, what, gen, isPaged, flag] {
            if (! *flag || gen != generation)
                return;
            loading = false;
            errorText = res->error;
            if (res->error.isNotEmpty())
            {
                if (! append)
                {
                    results.clear();
                    total = 0;
                    table.updateContent();
                    setTitle (what, {});
                }
                repaint();
                return;
            }
            paged = isPaged;
            if (! append)
            {
                const int selRow = table.getSelectedRow();
                const auto selected = juce::isPositiveAndBelow (selRow, results.size()) ? results[selRow].path : juce::String();
                results = res->samples;
                table.updateContent();
                table.getVerticalScrollBar().setCurrentRangeStart (0);
                const juce::ScopedValueSetter<bool> quiet (autoplay, false);   // re-selecting doesn't play
                table.deselectAllRows();
                for (int i = 0; i < results.size() && selected.isNotEmpty(); ++i)
                    if (results.getReference (i).path == selected)
                    {
                        table.selectRow (i, false, true);
                        break;
                    }
            }
            else
            {
                results.addArray (res->samples);
                table.updateContent();
            }
            if (! append && enterListOnLoad)
            {
                enterListOnLoad = false;
                enterList();
            }
            total = isPaged ? res->total : results.size();
            juce::String sub = juce::String (total) + (total == 1 ? " sample" : " samples");
            if (! res->skipped.isEmpty())
                sub << " (" << res->skipped.joinIntoString (", ") << " unknown)";
            setTitle (what, sub);
            const bool canShuffle = isPaged && search.getText().trim().isEmpty() && similarTitle.isEmpty();
            shuffleButton.setVisible (canShuffle);
            unshuffleButton.setVisible (canShuffle && shuffled);
            repaint();
        });
    });
}

void FetchEditor::loadCatalog()
{
    auto* client = &proc.client;
    pool->addJob ([this, client, flag = alive] {
        juce::String err;
        auto cat = client->catalog (err);
        auto rec = client->recent();
        juce::MessageManager::callAsync ([this, cat, rec, err, flag] {
            if (! *flag)
                return;
            if (err.isEmpty())
            {
                catalog = cat;
                sidebar.setCatalog (cat);
            }
            sidebar.setRecent (rec);
            table.repaint();
        });
    });
}

void FetchEditor::enterList()
{
    if (table.getNumSelectedRows() == 0 && ! results.isEmpty())
        table.selectRow (0);   // plays it with Play on select, like a Down press in the list
}

void FetchEditor::commitSearch()
{
    const auto q = search.getText().trim();
    if (q.isEmpty() || q == committedQuery)
        return;
    committedQuery = q;
    auto* client = &proc.client;
    pool->addJob ([this, client, q, flag = alive] {
        auto rec = client->recent (q);
        juce::MessageManager::callAsync ([this, rec, flag] {
            if (*flag)
                sidebar.setRecent (rec);
        });
    });
}

// ---------------------------------------------------------------- table

void FetchEditor::paintRowBackground (juce::Graphics& g, int row, int, int h, bool selected)
{
    if (selected)
        g.fillAll (theme::selected);
    if (juce::isPositiveAndBelow (row, results.size()) && proc.isPreviewing() && proc.currentSample() != nullptr
        && proc.currentSample()->path == results.getReference (row).path)
    {
        g.setColour (theme::playing);
        g.fillRoundedRectangle (2.0f, 6.0f, 3.0f, (float) h - 12.0f, 1.5f);
    }
}

juce::String FetchEditor::keyCell (const SampleInfo& s) const
{
    if (s.key.isEmpty())
        return {};
    auto k = s.key + (s.keyEst ? " ~" : "");
    const auto pk = proc.projectKey();
    if (pk.isNotEmpty())
        if (const int st = keys::semitonesBetween (s.key, pk); st != 0)
            k << "  " << (st > 0 ? "+" : "") << st;
    return k;
}

juce::String FetchEditor::genreCell (const SampleInfo& s) const
{
    juce::StringArray out;
    for (auto& g : s.genres)
        if (auto l = sidebar.genreLabel (g); l.isNotEmpty())
            out.add (l);
    return out.joinIntoString (", ");
}

void FetchEditor::paintCell (juce::Graphics& g, int row, int col, int w, int h, bool)
{
    if (! juce::isPositiveAndBelow (row, results.size()))
        return;
    const auto& s = results.getReference (row);
    if (col == colHeart)
    {
        theme::drawIcon (g, theme::heart(), juce::Rectangle<float> ((float) w, (float) h).withSizeKeepingCentre (16.0f, 16.0f),
                         s.liked ? theme::liked : theme::fg, s.liked, 1.3f);
        return;
    }
    juce::String text;
    auto just = juce::Justification::centredLeft;
    switch (col)
    {
        case colName:  text = s.name; break;
        case colInst:  text = s.instLabel + (s.instEst && s.instLabel.isNotEmpty() ? " ~" : ""); break;
        case colGenre: text = genreCell (s); break;
        case colKey:   text = keyCell (s); break;
        case colBpm:   text = s.bpm > 0 ? fmtBpm (s.bpm) + (s.bpmEst ? " ~" : "") : juce::String(); just = juce::Justification::centredRight; break;
        case colLen:   text = fmtLen (s.dur); just = juce::Justification::centredRight; break;
        case colType:  text = s.type; break;
        case colPack:  text = withoutRoot (s.pack); break;
        default: break;
    }
    g.setColour (theme::fg);
    g.setFont (theme::font());
    g.drawText (text, 7, 0, w - 14, h, just, true);
}

juce::String FetchEditor::getCellTooltip (int row, int col)
{
    if (! juce::isPositiveAndBelow (row, results.size()))
        return {};
    const auto& s = results.getReference (row);
    if (col == colHeart)
        return s.liked ? "Unlike (L)" : "Like (L)";
    if (col == colKey && proc.projectKey().isNotEmpty() && s.key.isNotEmpty())
        return "Transposed to " + proc.projectKey() + " when played ("
               + juce::String (keys::semitonesBetween (s.key, proc.projectKey())) + " semitones)";
    if (col == colBpm && s.bpm > 0)
        return s.bpmEst ? "Tempo estimated from the audio" : "Tempo from the file name";
    if (col == colInst)
        return s.inst;
    return s.path;
}

void FetchEditor::playRow (int row)
{
    if (! juce::isPositiveAndBelow (row, results.size()))
        return;
    proc.play (results[row]);
    // fetch the rows the user is heading for while this one plays (network shares)
    static int lastRow = -1;
    const int dir = row < lastRow ? -1 : 1;
    lastRow = row;
    juce::StringArray next;
    for (int k : { row + dir, row + 2 * dir, row + 3 * dir, row - dir })
        if (juce::isPositiveAndBelow (k, results.size()))
            next.add (results[k].path);
    proc.warm (next);
}

void FetchEditor::selectedRowsChanged (int last)
{
    if (autoplay && table.getNumSelectedRows() == 1)
    {
        playRow (last);
        commitSearch();   // choosing a result saves the search to Recent searches
    }
}

void FetchEditor::cellClicked (int row, int col, const juce::MouseEvent& e)
{
    searchWanted = false;
    if (e.mods.isPopupMenu())
    {
        if (! table.isRowSelected (row))
            table.selectRow (row);
        showMenu (row);
        return;
    }
    if (col == colHeart)
    {
        toggleLike ({ row });
        return;
    }
    // clicking the playing row again starts it from the beginning
    if (! e.mods.isAnyModifierKeyDown() && table.getNumSelectedRows() == 1 && table.isRowSelected (row)
        && proc.currentSample() != nullptr && proc.currentSample()->path == results[row].path)
        playRow (row);
}

void FetchEditor::cellDoubleClicked (int row, int, const juce::MouseEvent&)
{
    playRow (row);
}

void FetchEditor::returnKeyPressed (int row)
{
    playRow (row);
}

void FetchEditor::sortOrderChanged (int col, bool forwards)
{
    if (col == colHeart)
        return;
    sortColumn = col;
    sortForwards = forwards;
    shuffled = false;
    saveSettings();
    if (similarTitle.isEmpty())
        reload();
}

void FetchEditor::toggleLike (const juce::Array<int>& rows)
{
    juce::StringArray paths;
    bool anyUnliked = false;
    for (int r : rows)
        if (juce::isPositiveAndBelow (r, results.size()))
        {
            paths.add (results[r].path);
            anyUnliked = anyUnliked || ! results[r].liked;
        }
    if (paths.isEmpty())
        return;
    const int on = anyUnliked ? 1 : 0;   // several rows: like all unless all are liked already
    for (int r : rows)
        if (juce::isPositiveAndBelow (r, results.size()))
            results.getReference (r).liked = on == 1;
    table.repaint();
    auto* client = &proc.client;
    pool->addJob ([this, client, paths, on, flag = alive] {
        juce::String err;
        client->like (paths, on, err);
        juce::MessageManager::callAsync ([this, err, flag] {
            if (! *flag)
                return;
            if (err.isNotEmpty())
                setTitle (title.getText(), err);
        });
    });
}

void FetchEditor::showMenu (int row)
{
    if (! juce::isPositiveAndBelow (row, results.size()))
        return;
    juce::Array<int> rows;
    const auto sel = table.getSelectedRows();
    for (int i = 0; i < sel.size(); ++i)
        rows.add (sel[i]);
    const auto s = results[row];
    juce::PopupMenu m;   // as the Fetch window's menu
    auto item = [&m] (int id, const juce::String& text, const juce::String& key = {}) {
        juce::PopupMenu::Item i (text);
        i.itemID = id;
        i.shortcutKeyDescription = key;
        m.addItem (i);
    };
    item (1, "Like / Unlike", "L");
    item (2, juce::String::fromUTF8 ("Similar sounds\xe2\x80\xa6"), "S");
    item (5, juce::String::fromUTF8 ("Edit tags\xe2\x80\xa6"), "E");
    m.addSeparator();
    item (3, "Show in folder");
    item (4, "Copy path");
    m.showMenuAsync (juce::PopupMenu::Options().withTargetComponent (&table).withMousePosition(),
                     [this, rows, row, s, flag = alive] (int r) {
                         if (! *flag)
                             return;
                         if (r == 1)
                             toggleLike (rows);
                         else if (r == 2)
                             showSimilar (row);
                         else if (r == 3)
                             localFile (s.path).revealToUser();
                         else if (r == 4)
                             juce::SystemClipboard::copyTextToClipboard (s.path);
                         else if (r == 5)
                             editTags (rows);
                     });
}

juce::var FetchEditor::getDragSourceDescription (const juce::SparseSet<int>& rows)
{
    return rows.isEmpty() ? juce::var() : juce::var ("fetch-samples");
}

bool FetchEditor::shouldDropFilesWhenDraggedExternally (const juce::DragAndDropTarget::SourceDetails&,
                                                        juce::StringArray& files, bool& canMoveFiles)
{
    canMoveFiles = false;
    const auto sel = table.getSelectedRows();
    juce::String lastError;
    for (int i = 0; i < sel.size() && i < 32; ++i)
    {
        const int row = sel[i];
        if (! juce::isPositiveAndBelow (row, results.size()))
            continue;
        juce::String err;
        auto f = proc.fileForDrag (results[row], err);
        if (f.existsAsFile())
            files.add (dragItem (f));
        else if (err.isNotEmpty())
            lastError = err;
    }
    if (lastError.isNotEmpty())
        setTitle (title.getText(), lastError);
    return ! files.isEmpty();
}

bool FetchEditor::keyPressed (const juce::KeyPress& k)
{
    const auto c = k.getTextCharacter();
    // list navigation works wherever the focus is (except in the search field, which handles its own keys)
    for (auto nav : { juce::KeyPress::upKey, juce::KeyPress::downKey, juce::KeyPress::pageUpKey, juce::KeyPress::pageDownKey,
                      juce::KeyPress::homeKey, juce::KeyPress::endKey })
        if (k.getKeyCode() == nav)
        {
            searchWanted = false;
            table.grabKeyboardFocus();
            return table.keyPressed (k);
        }
    if (k == juce::KeyPress::spaceKey)
    {
        if (proc.isPreviewing())
            proc.togglePause();
        else
            playRow (table.getSelectedRow());
        return true;
    }
    if (k == juce::KeyPress::escapeKey)
    {
        proc.stop();
        return true;
    }
    if (k == juce::KeyPress::leftKey || k == juce::KeyPress::rightKey)
    {
        if (proc.isPreviewing())
            proc.seek (juce::jlimit (0.0, 1.0, proc.playFraction() + (k == juce::KeyPress::leftKey ? -0.1 : 0.1)));
        return true;
    }
    if (c == 'l' || c == 'L')
    {
        juce::Array<int> rows;
        const auto sel = table.getSelectedRows();
        for (int i = 0; i < sel.size(); ++i)
            rows.add (sel[i]);
        toggleLike (rows);
        return true;
    }
    if (c == 's' || c == 'S')
    {
        showSimilar (table.getSelectedRow());
        return true;
    }
    if (c == 'e' || c == 'E')
    {
        juce::Array<int> rows;
        const auto sel = table.getSelectedRows();
        for (int i = 0; i < sel.size(); ++i)
            rows.add (sel[i]);
        editTags (rows);
        return true;
    }
    if (c == '/' || k == juce::KeyPress ('f', juce::ModifierKeys::commandModifier, 0))
    {
        search.grabKeyboardFocus();
        search.selectAll();
        return true;
    }
    return false;
}

// ---------------------------------------------------------------- timer

void FetchEditor::timerCallback()
{
    const auto nowMs = juce::Time::getMillisecondCounter();
    if (searchDueAt != 0 && nowMs >= searchDueAt)
    {
        searchDueAt = 0;
        saveSettings();
        reload();
    }
    if (loading)
    {
        spin += 0.35f;
        repaint (search.getBoundsInParent());   // the spinner (transformed with the UI scale)
    }
    if (nowMs - themeCheckedMs > 2000)
    {
        themeCheckedMs = nowMs;
        checkTheme();
        checkAppearance();
    }

    // host status in the sidebar; re-filter when the host tempo changes and "Only host tempo" is on
    const double bpm = proc.hostBpm.load();
    juce::String h;
    if (proc.hostHasTempo.load())
        h << fmtBpm (bpm) << " BPM " << (proc.hostPlaying.load() ? juce::String::fromUTF8 ("\xe2\x96\xb6") : juce::String::fromUTF8 ("\xe2\x96\xa0"));
    else
        h << "no host tempo";
    sidebar.setHostStatus (h);
    if ((bool) proc.settings.getProperty ("onlyTempo", false) && similarTitle.isEmpty() && std::abs (bpm - filteredBpm) > 0.05 && ! loading)
    {
        if (filteredBpm > 0)
            reload();
        filteredBpm = bpm;
    }

    // player bar
    const bool playing = proc.isPreviewing();
    const auto* cur = proc.currentSample();
    playButton.icon = playing && ! proc.isPaused() ? theme::pause() : theme::play();
    playButton.repaint();
    now.setText (cur != nullptr && playing ? cur->name : juce::String(), juce::dontSendNotification);
    seek.enabled = playing;
    if (playing && ! seek.isMouseButtonDown())
        seek.value = proc.playFraction();
    seek.repaint();
    const double len = proc.playLength();
    timeLabel.setText (playing ? fmtTime (proc.playFraction() * len) + " / " + fmtTime (len) : juce::String(), juce::dontSendNotification);

    if (auto e = proc.takeError(); e.isNotEmpty())
        setTitle (title.getText(), e);

    // next page when scrolled near the end
    if (! loading && paged && results.size() < total)
    {
        const int lastVisible = table.getRowContainingPosition (10, table.getHeight() - 2);
        if (lastVisible < 0 || lastVisible > results.size() - 30)
            reload (true);
    }
    if (++repaintTick % 4 == 0)
        table.repaint();   // the playing-row marker
}

// ---------------------------------------------------------------- focus, theme, tags

void FetchEditor::mouseDown (const juce::MouseEvent& e)
{
    // remember a click into the search field; any other click cancels it
    searchWanted = e.eventComponent == &search || search.isParentOf (e.eventComponent);
    if (searchWanted)
        searchClickMs = juce::Time::getMillisecondCounter();
}

void FetchEditor::globalFocusChanged (juce::Component* focused)
{
    // A host gives the plugin window keyboard focus after the click that went into the search field,
    // and JUCE then restores the component focused before (the list). Keep the field the user clicked.
    if (searchWanted && focused != &search && isParentOf (focused)
        && juce::Time::getMillisecondCounter() - searchClickMs < 400)
        juce::MessageManager::callAsync ([this, flag = alive] {
            if (*flag && searchWanted && ! search.hasKeyboardFocus (false))
                search.grabKeyboardFocus();
        });
}

void FetchEditor::applyTheme()
{
    lnf.refresh();
    search.setColour (juce::TextEditor::textColourId, theme::fg);
    search.applyColourToAllText (theme::fg);
    search.setTextToShowWhenEmpty (juce::String::fromUTF8 ("Search samples \xe2\x80\x93 e.g. dark punchy kick, bpm:124, key:Am, +loop"), theme::dim);
    title.setColour (juce::Label::textColourId, theme::fg);
    subtitle.setColour (juce::Label::textColourId, theme::dim);
    now.setColour (juce::Label::textColourId, theme::fg);
    timeLabel.setColour (juce::Label::textColourId, theme::dim);
    table.setColour (juce::ListBox::backgroundColourId, theme::view);
    sendLookAndFeelChange();
    repaint();
    sidebar.repaint();
    if (tagEditor)
        tagEditor->repaint();
}

void FetchEditor::checkTheme()
{
    if (themeChecking)
        return;
    themeChecking = true;
    pool->addJob ([this, flag = alive] {
        bool dark = false;
        juce::Colour accent;
        theme::detectScheme (dark, accent);
        juce::MessageManager::callAsync ([this, dark, accent, flag] {
            if (! *flag)
                return;
            themeChecking = false;
            if (dark != theme::dark || accent != theme::accentBg)
            {
                theme::setScheme (dark, accent);
                applyTheme();
            }
        });
    });
}

void FetchEditor::editTags (const juce::Array<int>& rows)
{
    juce::Array<SampleInfo> samples;
    for (int r : rows)
        if (juce::isPositiveAndBelow (r, results.size()))
            samples.add (results[r]);
    if (samples.isEmpty() || catalog.isVoid())
        return;
    tagEditor = std::make_unique<TagEditor> (*this, samples, catalog);
    addAndMakeVisible (*tagEditor);
    tagEditor->setBounds (virtualBounds());
    tagEditor->setTransform (juce::AffineTransform::scale (theme::uiScale));
    tagEditor->grabKeyboardFocus();
}

void FetchEditor::tagsSaved (const juce::var& details)
{
    // the service answers {path: details}: update the rows in place
    if (auto* o = details.getDynamicObject())
        for (auto& r : results)
            if (o->hasProperty (r.path))
            {
                const bool liked = r.liked;
                r = SampleInfo::fromJson (r.path, o->getProperty (r.path));
                r.liked = liked;
            }
    table.repaint();
    closeTagEditor();
}

void FetchEditor::closeTagEditor()
{
    juce::MessageManager::callAsync ([this, flag = alive] {
        if (*flag)
        {
            tagEditor.reset();
            table.grabKeyboardFocus();
        }
    });
}

juce::AudioProcessorEditor* FetchProcessor::createEditor()
{
    return new FetchEditor (*this);
}
