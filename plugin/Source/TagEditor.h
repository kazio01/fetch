#pragma once
#include "PluginEditor.h"

// "Edit tags…" (as the Fetch window's dialog): type, instrument (tree) and style tags for the
// selected samples. Saved by the search service to korjaukset.json; for several samples only the
// changed fields are saved. An overlay over the plugin window (a separate window doesn't work well
// inside every host).
class TagEditor : public juce::Component
{
public:
    TagEditor (FetchEditor&, const juce::Array<SampleInfo>& samples, const juce::var& catalog);
    ~TagEditor() override;

    void paint (juce::Graphics&) override;
    void resized() override;
    bool keyPressed (const juce::KeyPress&) override;
    void mouseDown (const juce::MouseEvent&) override;

private:
    struct Body;
    void render();
    void save (bool automatic);
    juce::var changes() const;
    juce::StringArray pathOf (const juce::String& node) const;
    const juce::var* nodeById (const juce::String& id) const;

    FetchEditor& ed;
    juce::Array<SampleInfo> samples;
    juce::var catalog;
    juce::String type, inst;
    juce::StringArray path;           // instrument per level: ["drums", "drums/hat", "drums/hat/closed"]
    juce::StringArray tags, touchedTags;
    juce::StringArray touched;        // "type", "inst", "tags"
    juce::Rectangle<int> card;
    juce::Viewport viewport;
    std::unique_ptr<Body> body;
    juce::TextButton automatic { "Automatic" }, cancel { "Cancel" }, saveButton { "Save" };
    juce::String error;
    bool saving = false;
};
