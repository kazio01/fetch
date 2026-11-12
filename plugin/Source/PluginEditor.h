#pragma once
#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_gui_basics/juce_gui_basics.h>
#include "PluginProcessor.h"
#include "Theme.h"
#include "Sidebar.h"

// A flat icon button (libadwaita .flat button with a symbolic icon).
class IconButton : public juce::Button
{
public:
    IconButton (juce::Path p, const juce::String& tip, bool stroke = false) : juce::Button (tip), icon (std::move (p)), stroked (stroke)
    {
        setTooltip (tip);
        setWantsKeyboardFocus (false);
    }
    void paintButton (juce::Graphics&, bool over, bool down) override;
    juce::Path icon;
    bool stroked;
    bool active = false;   // drawn as toggled on
};

// The player bar's position slider (also shows where a synced loop is).
class SeekBar : public juce::Component
{
public:
    void paint (juce::Graphics&) override;
    void mouseDown (const juce::MouseEvent& e) override { mouseDrag (e); }
    void mouseDrag (const juce::MouseEvent&) override;
    double value = 0.0;
    bool enabled = false;
    std::function<void (double)> onSeek;
};

class TagEditor;

class FetchEditor : public juce::AudioProcessorEditor,
                    public juce::DragAndDropContainer,
                    private juce::TableListBoxModel,
                    private juce::Timer,
                    private juce::FocusChangeListener
{
public:
    explicit FetchEditor (FetchProcessor&);
    ~FetchEditor() override;

    void paint (juce::Graphics&) override;
    void paintOverChildren (juce::Graphics&) override;
    void resized() override;
    bool keyPressed (const juce::KeyPress&) override;

    bool shouldDropFilesWhenDraggedExternally (const juce::DragAndDropTarget::SourceDetails&,
                                               juce::StringArray& files, bool& canMoveFiles) override;

private:
    // TableListBoxModel
    int getNumRows() override { return results.size(); }
    void paintRowBackground (juce::Graphics&, int row, int w, int h, bool selected) override;
    void paintCell (juce::Graphics&, int row, int col, int w, int h, bool selected) override;
    void selectedRowsChanged (int lastRowSelected) override;
    void cellClicked (int row, int col, const juce::MouseEvent&) override;
    void cellDoubleClicked (int row, int col, const juce::MouseEvent&) override;
    void returnKeyPressed (int row) override;
    void sortOrderChanged (int newSortColumnId, bool isForwards) override;
    juce::var getDragSourceDescription (const juce::SparseSet<int>& rows) override;
    juce::String getCellTooltip (int row, int col) override;

    void timerCallback() override;
    void globalFocusChanged (juce::Component* focused) override;
    void applyTheme();
    void checkTheme();
    // Appearance: the editor is laid out in "virtual" pixels (its size / theme::uiScale) and every child
    // is drawn through a scale transform, so the whole UI grows or shrinks like a zoom.
    juce::Rectangle<int> virtualBounds() const;
    void checkAppearance();
    void openSettings (juce::Component& anchor);
    // Player options (the old Play on select button): Play on select, Loop preview, loop length /2, x2, Auto
    void openPlayerMenu();
    const SampleInfo* lengthTarget() const;   // the sample the loop length applies to (playing, else selected)
    juce::String lengthText() const;
    void changeLength (double factor);        // 0.5, 2, or 0 = automatic
    void editTags (const juce::Array<int>& rows);
    void tagsSaved (const juce::var& details);
    void closeTagEditor();
    void mouseDown (const juce::MouseEvent&) override;

    // loading
    void reload (bool append = false);
    void showSimilar (int row);
    void runQuery (std::function<PageResult()> fn, bool append, const juce::String& title, bool paged);
    void loadCatalog();
    juce::String allFilters() const;
    juce::String order() const;
    void commitSearch();
    void openView (const juce::String& v);

    // actions
    void playRow (int row);
    void toggleLike (const juce::Array<int>& rows);
    void showMenu (int row);
    void saveSettings();
    juce::String keyCell (const SampleInfo& s) const;
    juce::String genreCell (const SampleInfo& s) const;
    void setTitle (const juce::String& t, const juce::String& sub);

    FetchProcessor& proc;
    FetchLookAndFeel lnf;
    friend class TagEditor;
    Sidebar sidebar;

    juce::TextEditor search;
    struct KeyHook : juce::KeyListener   // keys the search field would swallow (Down: into the list)
    {
        std::function<bool (const juce::KeyPress&)> fn;
        bool keyPressed (const juce::KeyPress& k, juce::Component*) override { return fn && fn (k); }
    } searchKeys;
    bool enterListOnLoad = false;       // Down in the search field before its results arrived
    void enterList();
    juce::Label title, subtitle;
    IconButton shuffleButton { theme::shuffle(), "Shuffle - every click gives a new random order", true },
               unshuffleButton { theme::sortAscending(), "Back to folder order", true };
    juce::TableListBox table { "results", this };
    juce::TooltipWindow tooltips { this, 600 };

    // player bar
    IconButton playButton { theme::play(), "Play / pause (Space)" };
    juce::Label now, timeLabel;
    SeekBar seek;
    IconButton autoplayButton { theme::autoplay(), "Player options", true };
    juce::Slider volume { juce::Slider::LinearHorizontal, juce::Slider::NoTextBox };
    std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> volumeAttachment;

    juce::Array<SampleInfo> results;
    juce::String view = "ALL";          // "ALL" or a collection folder
    juce::String similarTitle;          // non-empty: showing Similar results
    juce::String committedQuery;
    int total = 0;
    bool loading = false, paged = false, shuffled = false, autoplay = true;
    int seed = 0, sortColumn = 0;
    bool sortForwards = true;
    int generation = 0;
    juce::uint32 searchDueAt = 0;
    double filteredBpm = 0;
    juce::String errorText;
    bool searchWanted = false;          // the user clicked into the search field (the host may move focus back)
    juce::uint32 searchClickMs = 0;
    juce::uint32 themeCheckedMs = 0;
    bool themeChecking = false;
    juce::var catalog;
    std::unique_ptr<TagEditor> tagEditor;
    float spin = 0.0f;
    int repaintTick = 0;
    std::unique_ptr<juce::ThreadPool> pool = std::make_unique<juce::ThreadPool> (2);
    std::shared_ptr<bool> alive = std::make_shared<bool> (true);

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (FetchEditor)
};
