#pragma once
#include <juce_gui_basics/juce_gui_basics.h>

// The look of the Fetch window (GTK 4 / libadwaita) in JUCE: light or dark like the system,
// with the system accent colour. Values measured from the Fetch window's own rendering.
namespace theme
{
    // palette (set by setScheme; read at paint time)
    inline juce::Colour window, view, sidebar, sidebarEdge, border, fg, dim, faint, accentBg, accent,
        buttonBg, buttonHover, buttonPressed, entryBg, selected, sideSelected, hover, liked, playing,
        trough, knob, popup, tooltipBg, tooltipFg;
    inline bool dark = false;
    // Settings > Appearance (appearance.json, shared with the Fetch window): the whole editor is drawn
    // scaled by uiScale (a transform, see FetchEditor::resized); fontScale enlarges text only.
    inline float uiScale = 1.0f, fontScale = 1.0f;

    void setScheme (bool isDark, juce::Colour accentColour);
    // The system's choice: GNOME (gsettings color-scheme + accent-color) on Linux, the OS setting elsewhere.
    // Blocking (runs a process on Linux): call it off the message thread.
    void detectScheme (bool& isDark, juce::Colour& accentColour);

    // Adwaita Sans 11 pt = 14.67 px, as in GNOME
    constexpr float body = 14.67f;
    juce::Font font (float size = body, bool bold = false);

    // Icons (symbolic, drawn into a 16×16 box)
    juce::Path heart();
    juce::Path play();
    juce::Path pause();
    juce::Path shuffle();
    juce::Path sortAscending();
    juce::Path chevronRight();
    juce::Path chevronDown();
    juce::Path search();
    juce::Path folder();
    juce::Path trash();
    juce::Path list();
    juce::Path note();
    juce::Path autoplay();
    void drawIcon (juce::Graphics&, const juce::Path&, juce::Rectangle<float>, juce::Colour, bool filled = true,
                   float stroke = 1.6f);

    juce::Image logo();   // the Fetch logo on a transparent background: fetch-mark.png (black), fetch-mark-dark.png (white) in the dark scheme
    constexpr float small = 13.2f;   // the filter section's text (0.9em, regular like the rest)
}

class FetchLookAndFeel : public juce::LookAndFeel_V4
{
public:
    FetchLookAndFeel() { refresh(); }
    void refresh();   // after theme::setScheme

    void drawButtonBackground (juce::Graphics&, juce::Button&, const juce::Colour&, bool over, bool down) override;
    void drawButtonText (juce::Graphics&, juce::TextButton&, bool over, bool down) override;
    juce::Font getTextButtonFont (juce::TextButton&, int) override { return theme::font (theme::body, true); }
    void drawToggleButton (juce::Graphics&, juce::ToggleButton&, bool over, bool down) override;
    void fillTextEditorBackground (juce::Graphics&, int w, int h, juce::TextEditor&) override;
    void drawTextEditorOutline (juce::Graphics&, int w, int h, juce::TextEditor&) override;
    void drawComboBox (juce::Graphics&, int w, int h, bool down, int bx, int by, int bw, int bh, juce::ComboBox&) override;
    juce::Font getComboBoxFont (juce::ComboBox&) override { return theme::font (theme::small); }
    void positionComboBoxText (juce::ComboBox&, juce::Label&) override;
    void drawPopupMenuBackground (juce::Graphics&, int w, int h) override;
    void drawPopupMenuItem (juce::Graphics&, const juce::Rectangle<int>&, bool isSeparator, bool isActive,
                            bool isHighlighted, bool isTicked, bool hasSubMenu, const juce::String& text,
                            const juce::String& shortcutKeyText, const juce::Drawable* icon,
                            const juce::Colour* textColour) override;
    juce::Font getPopupMenuFont() override { return theme::font(); }
    int getPopupMenuBorderSize() override { return 6; }
    void drawScrollbar (juce::Graphics&, juce::ScrollBar&, int x, int y, int w, int h, bool vertical,
                        int thumbStart, int thumbSize, bool over, bool down) override;
    int getDefaultScrollbarWidth() override { return 10; }
    void drawTableHeaderBackground (juce::Graphics&, juce::TableHeaderComponent&) override;
    void drawTableHeaderColumn (juce::Graphics&, juce::TableHeaderComponent&, const juce::String& name, int columnId,
                                int w, int h, bool over, bool down, int flags) override;
    void drawLinearSlider (juce::Graphics&, int x, int y, int w, int h, float pos, float min, float max,
                           juce::Slider::SliderStyle, juce::Slider&) override;
    void drawTooltip (juce::Graphics&, const juce::String& text, int w, int h) override;
    juce::Rectangle<int> getTooltipBounds (const juce::String& text, juce::Point<int> pos, juce::Rectangle<int> parent) override;
    void drawCornerResizer (juce::Graphics&, int, int, bool, bool) override {}
};
