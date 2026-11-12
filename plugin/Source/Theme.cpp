#include "Theme.h"
#include "BinaryData.h"

namespace theme
{
static juce::String sansName()
{
    static const juce::String name = [] {
        const auto names = juce::Font::findAllTypefaceNames();
        for (auto* n : { "Adwaita Sans", "Cantarell", "Inter", "Segoe UI", "Noto Sans", "DejaVu Sans" })
            if (names.contains (n))
                return juce::String (n);
        return juce::Font::getDefaultSansSerifFontName();
    }();
    return name;
}

juce::Font font (float size, bool bold)
{
    return juce::Font (juce::FontOptions (sansName(), size * fontScale, bold ? juce::Font::bold : juce::Font::plain));
}

void setScheme (bool isDark, juce::Colour acc)
{
    dark = isDark;
    accentBg = acc;
    const auto ink = isDark ? juce::Colours::white : juce::Colour (0, 0, 6);
    const float inkAlpha = isDark ? 1.0f : 0.8f;
    fg = ink.withAlpha (inkAlpha);
    dim = ink.withAlpha (inkAlpha * 0.55f);
    faint = ink.withAlpha (inkAlpha * 0.3f);
    if (isDark)
    {
        window = juce::Colour (0xff222226);
        view = juce::Colour (0xff1d1d20);
        sidebar = juce::Colour (0xff2e2e32);
        sidebarEdge = juce::Colour (0xff1d1d22);
        border = juce::Colour (0xff3d3d40);
        sideSelected = juce::Colour (0xff434347);
        knob = juce::Colour (0xffd2d2d2);
        popup = juce::Colour (0xff36363a);
        accent = acc.interpolatedWith (juce::Colours::white, 0.35f);
    }
    else
    {
        window = juce::Colour (0xfffafafb);
        view = juce::Colours::white;
        sidebar = juce::Colour (0xffebebed);
        sidebarEdge = juce::Colour (0xffdbdbdd);
        border = juce::Colour (0xffe2e2e4);
        sideSelected = juce::Colour (0xffd8d8db);
        knob = juce::Colours::white;
        popup = juce::Colours::white;
        accent = acc.interpolatedWith (juce::Colours::black, 0.2f);
    }
    buttonBg = ink.withAlpha (isDark ? 0.10f : 0.08f);
    buttonHover = ink.withAlpha (isDark ? 0.15f : 0.12f);
    buttonPressed = ink.withAlpha (isDark ? 0.25f : 0.2f);
    entryBg = ink.withAlpha (isDark ? 0.10f : 0.08f);
    hover = ink.withAlpha (isDark ? 0.05f : 0.04f);
    trough = ink.withAlpha (isDark ? 0.15f : 0.12f);
    selected = acc.withAlpha (0.35f);
    liked = juce::Colour (isDark ? 0xffe62d42 : 0xffe01b24);
    playing = juce::Colour (isDark ? 0xff57e389 : 0xff2ec27e);
    tooltipBg = juce::Colour (0xf0101012);
    tooltipFg = juce::Colours::white;
}

static juce::Colour gnomeAccent (const juce::String& name)
{
    // libadwaita's accent colours (org.gnome.desktop.interface accent-color)
    static const std::pair<const char*, juce::uint32> table[] = {
        { "blue", 0xff3584e4 }, { "teal", 0xff2190a4 }, { "green", 0xff3a944a }, { "yellow", 0xffc88800 },
        { "orange", 0xffed5b00 }, { "red", 0xffe62d42 }, { "pink", 0xffd56199 }, { "purple", 0xff9141ac },
        { "slate", 0xff6f8396 } };
    for (auto& [n, c] : table)
        if (name == n)
            return juce::Colour (c);
    return juce::Colour (0xff3584e4);
}

void detectScheme (bool& isDark, juce::Colour& acc)
{
    acc = juce::Colour (0xff3584e4);
   #if ! JUCE_LINUX
    isDark = juce::Desktop::getInstance().isDarkModeActive();   // Windows: the "app mode" setting
   #else
    isDark = false;
    auto get = [] (const char* key) -> juce::String {
        juce::ChildProcess p;
        if (! p.start (juce::StringArray { "gsettings", "get", "org.gnome.desktop.interface", key }))
            return {};
        auto out = p.readAllProcessOutput().trim().unquoted();
        p.waitForProcessToFinish (2000);
        return out;
    };
    if (const auto scheme = get ("color-scheme"); scheme.isNotEmpty())
        isDark = scheme == "prefer-dark";
    if (const auto a = get ("accent-color"); a.isNotEmpty())
        acc = gnomeAccent (a);
   #endif
    // FETCH_THEME=light|dark overrides the system (tests, screenshots)
    if (const auto force = juce::SystemStats::getEnvironmentVariable ("FETCH_THEME", {}); force.isNotEmpty())
        isDark = force == "dark";
}

juce::Path heart()
{
    juce::Path p;
    p.startNewSubPath (8.0f, 14.0f);
    p.cubicTo (2.0f, 9.8f, 0.6f, 7.0f, 1.6f, 4.6f);
    p.cubicTo (2.6f, 2.2f, 6.0f, 1.6f, 8.0f, 4.4f);
    p.cubicTo (10.0f, 1.6f, 13.4f, 2.2f, 14.4f, 4.6f);
    p.cubicTo (15.4f, 7.0f, 14.0f, 9.8f, 8.0f, 14.0f);
    p.closeSubPath();
    return p;
}

juce::Path play()
{
    juce::Path p;
    p.addTriangle (4.0f, 2.5f, 4.0f, 13.5f, 13.5f, 8.0f);
    return p;
}

juce::Path pause()
{
    juce::Path p;
    p.addRoundedRectangle (3.5f, 2.5f, 3.2f, 11.0f, 0.8f);
    p.addRoundedRectangle (9.3f, 2.5f, 3.2f, 11.0f, 0.8f);
    return p;
}

juce::Path shuffle()
{
    juce::Path p;
    p.startNewSubPath (1.5f, 4.5f);
    p.lineTo (4.5f, 4.5f);
    p.cubicTo (8.0f, 4.5f, 8.0f, 11.5f, 11.5f, 11.5f);
    p.lineTo (14.0f, 11.5f);
    p.startNewSubPath (1.5f, 11.5f);
    p.lineTo (4.5f, 11.5f);
    p.cubicTo (8.0f, 11.5f, 8.0f, 4.5f, 11.5f, 4.5f);
    p.lineTo (14.0f, 4.5f);
    p.startNewSubPath (12.0f, 2.5f);
    p.lineTo (14.2f, 4.5f);
    p.lineTo (12.0f, 6.5f);
    p.startNewSubPath (12.0f, 9.5f);
    p.lineTo (14.2f, 11.5f);
    p.lineTo (12.0f, 13.5f);
    return p;
}

juce::Path sortAscending()
{
    juce::Path p;
    for (int i = 0; i < 3; ++i)
    {
        p.startNewSubPath (2.0f, 4.0f + i * 4.0f);
        p.lineTo (5.0f + i * 4.0f, 4.0f + i * 4.0f);
    }
    return p;
}

juce::Path chevronRight()
{
    juce::Path p;
    p.startNewSubPath (6.0f, 3.5f);
    p.lineTo (10.5f, 8.0f);
    p.lineTo (6.0f, 12.5f);
    return p;
}

juce::Path chevronDown()
{
    juce::Path p;
    p.startNewSubPath (3.5f, 6.0f);
    p.lineTo (8.0f, 10.5f);
    p.lineTo (12.5f, 6.0f);
    return p;
}

juce::Path search()
{
    juce::Path p;
    p.addEllipse (2.0f, 2.0f, 8.5f, 8.5f);
    p.startNewSubPath (9.3f, 9.3f);
    p.lineTo (14.0f, 14.0f);
    return p;
}

juce::Path folder()
{
    juce::Path p;
    p.startNewSubPath (1.5f, 4.0f);
    p.lineTo (1.5f, 13.0f);
    p.lineTo (14.5f, 13.0f);
    p.lineTo (14.5f, 5.5f);
    p.lineTo (7.5f, 5.5f);
    p.lineTo (6.0f, 3.0f);
    p.lineTo (2.5f, 3.0f);
    p.closeSubPath();
    return p;
}

juce::Path trash()
{
    // user-trash-symbolic (stroked, 16 × 16): lid, can, two ribs
    juce::Path p;
    p.startNewSubPath (2.5f, 4.0f);
    p.lineTo (13.5f, 4.0f);
    p.startNewSubPath (6.0f, 4.0f);
    p.lineTo (6.5f, 2.0f);
    p.lineTo (9.5f, 2.0f);
    p.lineTo (10.0f, 4.0f);
    p.startNewSubPath (4.0f, 4.0f);
    p.lineTo (4.8f, 14.0f);
    p.lineTo (11.2f, 14.0f);
    p.lineTo (12.0f, 4.0f);
    p.startNewSubPath (6.8f, 6.5f);
    p.lineTo (7.0f, 11.5f);
    p.startNewSubPath (9.2f, 6.5f);
    p.lineTo (9.0f, 11.5f);
    return p;
}

juce::Path list()
{
    juce::Path p;
    for (int i = 0; i < 3; ++i)
    {
        p.addEllipse (1.5f, 3.0f + i * 4.5f - 0.9f, 1.8f, 1.8f);
        p.startNewSubPath (5.0f, 3.0f + i * 4.5f);
        p.lineTo (14.5f, 3.0f + i * 4.5f);
    }
    return p;
}



juce::Path autoplay()
{
    juce::Path p;   // list with a play mark (media-playlist-consecutive)
    p.startNewSubPath (1.5f, 4.0f);
    p.lineTo (9.0f, 4.0f);
    p.startNewSubPath (1.5f, 8.0f);
    p.lineTo (9.0f, 8.0f);
    p.startNewSubPath (1.5f, 12.0f);
    p.lineTo (6.0f, 12.0f);
    p.startNewSubPath (10.5f, 9.0f);
    p.lineTo (10.5f, 14.5f);
    p.lineTo (14.8f, 11.75f);
    p.closeSubPath();
    return p;
}

juce::Path note()
{
    juce::Path p;
    p.addEllipse (2.0f, 10.0f, 5.0f, 4.0f);
    p.startNewSubPath (6.6f, 12.0f);
    p.lineTo (6.6f, 2.0f);
    p.lineTo (13.5f, 3.5f);
    p.lineTo (13.5f, 6.0f);
    p.lineTo (6.6f, 4.5f);
    return p;
}

void drawIcon (juce::Graphics& g, const juce::Path& p, juce::Rectangle<float> r, juce::Colour c, bool filled, float stroke)
{
    const auto t = juce::AffineTransform::scale (r.getWidth() / 16.0f, r.getHeight() / 16.0f).translated (r.getX(), r.getY());
    g.setColour (c);
    if (filled)
        g.fillPath (p, t);
    else
        g.strokePath (p, juce::PathStrokeType (stroke * r.getWidth() / 16.0f, juce::PathStrokeType::curved,
                                               juce::PathStrokeType::rounded), t);
}

juce::Image logo()
{
    static juce::Image light = juce::ImageCache::getFromMemory (BinaryData::fetchmark_png, BinaryData::fetchmark_pngSize);
    static juce::Image darkImg = juce::ImageCache::getFromMemory (BinaryData::fetchmarkdark_png, BinaryData::fetchmarkdark_pngSize);
    return dark ? darkImg : light;
}
} // namespace theme

// ---------------------------------------------------------------- LookAndFeel

void FetchLookAndFeel::refresh()
{
    using namespace theme;
    setColour (juce::ResizableWindow::backgroundColourId, window);
    setColour (juce::TextEditor::textColourId, fg);
    setColour (juce::TextEditor::highlightColourId, accentBg.withAlpha (0.35f));
    setColour (juce::TextEditor::highlightedTextColourId, fg);
    setColour (juce::CaretComponent::caretColourId, fg);
    setColour (juce::Label::textColourId, fg);
    setColour (juce::ListBox::backgroundColourId, view);
    setColour (juce::ComboBox::textColourId, fg);
    setColour (juce::ComboBox::arrowColourId, dim);
    setColour (juce::PopupMenu::backgroundColourId, popup);
    setColour (juce::PopupMenu::textColourId, fg);
    setColour (juce::PopupMenu::highlightedBackgroundColourId, buttonHover);
    setColour (juce::PopupMenu::highlightedTextColourId, fg);
    setColour (juce::TextButton::textColourOffId, fg);
    setColour (juce::TextButton::textColourOnId, fg);
    setColour (juce::ScrollBar::thumbColourId, dim);
    setColour (juce::TooltipWindow::textColourId, tooltipFg);
}

void FetchLookAndFeel::drawButtonBackground (juce::Graphics& g, juce::Button& b, const juce::Colour&, bool over, bool down)
{
    auto r = b.getLocalBounds().toFloat().reduced (0.5f);
    const bool flat = b.getProperties()["flat"];
    const bool suggested = b.getProperties()["suggested"];
    juce::Colour c = flat ? juce::Colours::transparentBlack : theme::buttonBg;
    if (suggested)
        c = down ? theme::accentBg.darker (0.2f) : over ? theme::accentBg.brighter (0.1f) : theme::accentBg;
    else if (down)
        c = theme::buttonPressed;
    else if (over)
        c = flat ? theme::buttonBg : theme::buttonHover;
    g.setColour (c);
    g.fillRoundedRectangle (r, 6.0f);
}

void FetchLookAndFeel::drawButtonText (juce::Graphics& g, juce::TextButton& b, bool, bool)
{
    const float size = b.getProperties().getWithDefault ("fontSize", theme::body);
    g.setFont (theme::font (size, true));
    g.setColour (b.getProperties()["suggested"] ? juce::Colours::white : b.isEnabled() ? theme::fg : theme::faint);
    g.drawFittedText (b.getButtonText(), b.getLocalBounds().reduced (6, 0), juce::Justification::centred, 1, 0.8f);
}

void FetchLookAndFeel::drawToggleButton (juce::Graphics& g, juce::ToggleButton& b, bool over, bool)
{
    // libadwaita switch at the right, label at the left (an AdwSwitchRow)
    auto r = b.getLocalBounds().toFloat();
    const float sh = juce::jmin (20.0f, r.getHeight() - 6.0f), swd = sh * 1.85f;
    auto sw = r.removeFromRight (swd).withSizeKeepingCentre (swd, sh);
    const bool on = b.getToggleState();
    g.setColour (on ? theme::accentBg : over ? theme::buttonPressed : theme::trough);
    g.fillRoundedRectangle (sw, sh / 2);
    const float k = sh - 4.0f;
    const auto knob = juce::Rectangle<float> (k, k).withCentre ({ on ? sw.getRight() - sh / 2 : sw.getX() + sh / 2, sw.getCentreY() });
    g.setColour (juce::Colours::white);
    g.fillEllipse (knob);
    g.setColour (b.isEnabled() ? theme::fg : theme::faint);
    g.setFont (theme::font (theme::small));
    g.drawFittedText (b.getButtonText(), r.toNearestInt().withTrimmedRight (6), juce::Justification::centredLeft, 1, 0.85f);
}

void FetchLookAndFeel::fillTextEditorBackground (juce::Graphics& g, int w, int h, juce::TextEditor&)
{
    g.setColour (theme::entryBg);
    g.fillRoundedRectangle (juce::Rectangle<float> ((float) w, (float) h), 8.0f);
}

void FetchLookAndFeel::drawTextEditorOutline (juce::Graphics& g, int w, int h, juce::TextEditor& e)
{
    if (e.hasKeyboardFocus (true))
    {
        g.setColour (theme::accentBg.withAlpha (0.5f));
        g.drawRoundedRectangle (juce::Rectangle<float> ((float) w, (float) h).reduced (1.0f), 8.0f, 2.0f);
    }
}

void FetchLookAndFeel::drawComboBox (juce::Graphics& g, int w, int h, bool, int, int, int, int, juce::ComboBox& box)
{
    auto r = juce::Rectangle<float> ((float) w, (float) h).reduced (0.5f);
    g.setColour (box.isMouseOver (true) ? theme::buttonHover : theme::buttonBg);
    g.fillRoundedRectangle (r, juce::jmin (6.0f, r.getHeight() / 2));
    const float a = juce::jmin (12.0f, (float) h - 8.0f);
    theme::drawIcon (g, theme::chevronDown(), { (float) w - a - 6.0f, ((float) h - a) / 2, a, a }, theme::dim, false, 1.8f);
}

void FetchLookAndFeel::positionComboBoxText (juce::ComboBox& box, juce::Label& label)
{
    label.setBounds (2, 0, box.getWidth() - 18, box.getHeight());
    label.setFont (theme::font (theme::small));
    label.setJustificationType (juce::Justification::centred);
}

void FetchLookAndFeel::drawPopupMenuBackground (juce::Graphics& g, int w, int h)
{
    g.fillAll (theme::popup);
    g.setColour (theme::border);
    g.drawRect (0, 0, w, h);
}

void FetchLookAndFeel::drawPopupMenuItem (juce::Graphics& g, const juce::Rectangle<int>& area, bool isSeparator, bool isActive,
                                          bool isHighlighted, bool, bool, const juce::String& text,
                                          const juce::String& shortcut, const juce::Drawable*, const juce::Colour*)
{
    if (isSeparator)
    {
        g.setColour (theme::border);
        g.fillRect (area.reduced (8, 0).withHeight (1).withY (area.getCentreY()));
        return;
    }
    auto r = area.reduced (4, 1);
    if (isHighlighted && isActive)
    {
        g.setColour (theme::buttonHover);
        g.fillRoundedRectangle (r.toFloat(), 6.0f);
    }
    g.setColour (isActive ? theme::fg : theme::faint);
    g.setFont (theme::font());
    auto t = r.reduced (10, 0);
    g.drawText (text, t, juce::Justification::centredLeft);
    if (shortcut.isNotEmpty())
    {
        g.setColour (theme::dim);
        g.drawText (shortcut, t, juce::Justification::centredRight);
    }
}

void FetchLookAndFeel::drawScrollbar (juce::Graphics& g, juce::ScrollBar&, int x, int y, int w, int h, bool vertical,
                                      int thumbStart, int thumbSize, bool over, bool down)
{
    juce::Rectangle<int> thumb = vertical ? juce::Rectangle<int> (x, thumbStart, w, thumbSize)
                                          : juce::Rectangle<int> (thumbStart, y, thumbSize, h);
    const float th = over || down ? 8.0f : 4.0f;
    auto t = thumb.toFloat();
    t = vertical ? t.withWidth (th).withX ((float) x + w - th - 2) : t.withHeight (th).withY ((float) y + h - th - 2);
    g.setColour (over || down ? theme::dim : theme::faint);
    g.fillRoundedRectangle (t.reduced (0, 1), th / 2);
}

void FetchLookAndFeel::drawTableHeaderBackground (juce::Graphics& g, juce::TableHeaderComponent&)
{
    g.fillAll (theme::view);
}

void FetchLookAndFeel::drawTableHeaderColumn (juce::Graphics& g, juce::TableHeaderComponent&, const juce::String& name, int,
                                              int w, int h, bool over, bool, int flags)
{
    if (over)
    {
        g.setColour (theme::hover);
        g.fillRect (0, 0, w, h);
    }
    auto r = juce::Rectangle<int> (w, h).reduced (8, 0);
    const bool sorted = (flags & (juce::TableHeaderComponent::sortedForwards | juce::TableHeaderComponent::sortedBackwards)) != 0;
    g.setColour (sorted ? theme::fg : theme::dim);
    g.setFont (theme::font (12.5f, true));
    if (sorted)
    {
        auto a = r.removeFromRight (12).toFloat().withSizeKeepingCentre (8, 8);
        juce::Path p;
        if (flags & juce::TableHeaderComponent::sortedForwards)
            p.addTriangle (a.getX(), a.getBottom(), a.getRight(), a.getBottom(), a.getCentreX(), a.getY());
        else
            p.addTriangle (a.getX(), a.getY(), a.getRight(), a.getY(), a.getCentreX(), a.getBottom());
        g.fillPath (p);
    }
    g.drawFittedText (name, r, juce::Justification::centredLeft, 1, 0.9f);
}

void FetchLookAndFeel::drawLinearSlider (juce::Graphics& g, int x, int y, int w, int h, float pos, float, float,
                                         juce::Slider::SliderStyle, juce::Slider& s)
{
    const float cy = (float) y + h * 0.5f;
    auto track = juce::Rectangle<float> ((float) x, cy - 3.0f, (float) w, 6.0f);
    g.setColour (theme::trough);
    g.fillRoundedRectangle (track, 3.0f);
    g.setColour (theme::accentBg);
    g.fillRoundedRectangle (track.withRight (pos), 3.0f);
    const float r = s.isMouseOverOrDragging() ? 10.0f : 9.0f;
    g.setColour (juce::Colour (0x33000000));
    g.fillEllipse (pos - r, cy - r + 1.0f, r * 2, r * 2);
    g.setColour (theme::knob);
    g.fillEllipse (pos - r, cy - r, r * 2, r * 2);
}

void FetchLookAndFeel::drawTooltip (juce::Graphics& g, const juce::String& text, int w, int h)
{
    g.setColour (theme::tooltipBg);
    g.fillRoundedRectangle (juce::Rectangle<float> ((float) w, (float) h), 6.0f);
    g.setColour (theme::tooltipFg);
    g.setFont (theme::font (13.0f));
    g.drawFittedText (text, juce::Rectangle<int> (w, h).reduced (8, 4), juce::Justification::centredLeft, 6);
}

juce::Rectangle<int> FetchLookAndFeel::getTooltipBounds (const juce::String& text, juce::Point<int> pos, juce::Rectangle<int> parent)
{
    juce::AttributedString s;
    s.append (text, theme::font (13.0f));
    juce::TextLayout tl;
    tl.createLayoutWithBalancedLineLengths (s, 380.0f);
    const int w = (int) tl.getWidth() + 18, h = (int) tl.getHeight() + 10;
    return juce::Rectangle<int> (pos.x > parent.getCentreX() ? pos.x - (w + 12) : pos.x + 12,
                                 pos.y > parent.getCentreY() ? pos.y - (h + 6) : pos.y + 18, w, h)
        .constrainedWithin (parent);
}
